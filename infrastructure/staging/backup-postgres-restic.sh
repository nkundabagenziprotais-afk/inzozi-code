#!/usr/bin/env bash
set -euo pipefail
umask 077

APP_ROOT="${APP_ROOT:-/srv/inzozi-code/application}"
ENV_FILE="${ENV_FILE:-/srv/inzozi-code/.env.staging}"
RESTIC_ENV_FILE="${RESTIC_ENV_FILE:-/srv/inzozi-code/secrets/restic.env}"
LOCK_FILE="${LOCK_FILE:-/run/lock/inzozi-code-postgres-backup.lock}"
STAGING_COMPOSE="${APP_ROOT}/infrastructure/staging/docker-compose.staging.yml"

log() {
  printf '%s\n' "$1"
}

fail() {
  printf 'BACKUP_ERROR=%s\n' "$1" >&2
  exit 1
}

require_private_file() {
  local path="$1"
  local mode

  [[ -f "$path" ]] || fail "missing-required-secret-file"

  mode="$(stat -c '%a' "$path")"

  case "$mode" in
    400|600)
      ;;
    *)
      fail "secret-file-mode-must-be-400-or-600"
      ;;
  esac
}

[[ "${EUID}" -eq 0 ]] || fail "must-run-as-root"
[[ -d "${APP_ROOT}/.git" ]] || fail "missing-git-checkout"
[[ -f "$ENV_FILE" ]] || fail "missing-staging-env"
[[ "$(stat -c '%a' "$ENV_FILE")" == "600" ]] || fail "staging-env-mode-must-be-600"
[[ -f "$STAGING_COMPOSE" ]] || fail "missing-staging-compose"
command -v docker >/dev/null 2>&1 || fail "docker-unavailable"
command -v restic >/dev/null 2>&1 || fail "restic-unavailable"
command -v flock >/dev/null 2>&1 || fail "flock-unavailable"

require_private_file "$RESTIC_ENV_FILE"

# shellcheck disable=SC1090
set -a
source "$RESTIC_ENV_FILE"
set +a

[[ -n "${RESTIC_REPOSITORY:-}" ]] || fail "restic-repository-unset"
[[ -n "${RESTIC_PASSWORD_FILE:-}" ]] || fail "restic-password-file-unset"
require_private_file "$RESTIC_PASSWORD_FILE"

mkdir -p "$(dirname "$LOCK_FILE")"
exec 9>"$LOCK_FILE"

if ! flock -n 9; then
  fail "backup-already-running"
fi

cd "$APP_ROOT"

COMPOSE=(
  docker compose
  --env-file "$ENV_FILE"
  -f docker-compose.yml
  -f "$STAGING_COMPOSE"
)

POSTGRES_CID="$(
  "${COMPOSE[@]}" ps -q postgres </dev/null
)"

[[ -n "$POSTGRES_CID" ]] || fail "postgres-container-not-found"

POSTGRES_RUNNING="$(
  docker inspect --format '{{.State.Running}}' "$POSTGRES_CID" </dev/null
)"

[[ "$POSTGRES_RUNNING" == "true" ]] || fail "postgres-not-running"

if ! docker exec "$POSTGRES_CID" sh -lc '
  set -eu
  DB="${POSTGRES_DB:-inzozi_code}"
  USER="${POSTGRES_USER:-inzozi_code}"
  pg_isready -U "$USER" -d "$DB" >/dev/null
' </dev/null; then
  fail "postgres-not-ready"
fi

if ! restic snapshots --no-lock --json >/dev/null; then
  fail "restic-repository-unreachable"
fi

STAMP="$(date -u '+%Y%m%dT%H%M%SZ')"
DUMP_NAME="postgres/inzozi-code-${STAMP}.dump"
START_EPOCH="$(date +%s)"

log "ISSUE39_POSTGRES_BACKUP_START=YES"
log "BACKUP_TIMESTAMP_UTC=${STAMP}"
log "BACKUP_PLAINTEXT_LOCAL_FILE=NO"
log "BACKUP_SECRET_VALUES_PRINTED=NO"

set +e

docker exec "$POSTGRES_CID" sh -lc '
  set -eu
  DB="${POSTGRES_DB:-inzozi_code}"
  USER="${POSTGRES_USER:-inzozi_code}"
  exec pg_dump \
    --format=custom \
    --compress=6 \
    --no-owner \
    --no-privileges \
    -U "$USER" \
    -d "$DB"
' </dev/null |
restic backup \
  --quiet \
  --stdin \
  --stdin-filename "$DUMP_NAME" \
  --tag inzozi-code \
  --tag staging \
  --tag postgres

BACKUP_RC=$?
set -e

[[ "$BACKUP_RC" -eq 0 ]] || fail "pg-dump-or-restic-backup-failed"

if ! restic forget \
  --quiet \
  --tag postgres \
  --group-by tags \
  --keep-within 24h \
  --keep-daily 7 \
  --keep-weekly 4 \
  --keep-monthly 3
then
  fail "restic-retention-failed-after-successful-backup"
fi

END_EPOCH="$(date +%s)"
DURATION="$((END_EPOCH - START_EPOCH))"

log "POSTGRES_BACKUP_UPLOAD=PASS"
log "RESTIC_RETENTION_POLICY=PASS"
log "BACKUP_DURATION_SECONDS=${DURATION}"
log "BACKUP_RPO_TARGET_HOURS=6"
log "BACKUP_ENCRYPTION=RESTIC_CLIENT_SIDE"
log "BACKUP_OFF_HOST_REPOSITORY=CONFIGURED"
log "BACKUP_SECRET_VALUES_PRINTED=NO"
log "ISSUE39_POSTGRES_BACKUP=PASS"
