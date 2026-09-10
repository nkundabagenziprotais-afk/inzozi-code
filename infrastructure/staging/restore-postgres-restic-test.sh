#!/usr/bin/env bash
set -euo pipefail
umask 077

APP_ROOT="${APP_ROOT:-/srv/inzozi-code/application}"
RESTIC_ENV_FILE="${RESTIC_ENV_FILE:-/srv/inzozi-code/secrets/restic.env}"
DIAGNOSTIC_LABEL='issue39-restore'
POSTGRES_IMAGE="${POSTGRES_IMAGE:-postgres:16-alpine}"

CONTAINER=''
VOLUME=''

fail() {
  printf 'RESTORE_TEST_ERROR=%s\n' "$1" >&2
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

cleanup() {
  set +e

  if [[ -n "$CONTAINER" ]] && docker inspect "$CONTAINER" >/dev/null 2>&1; then
    LABEL_VALUE="$(
      docker inspect \
        --format '{{ index .Config.Labels "com.inzozi.code.diagnostic" }}' \
        "$CONTAINER" \
        2>/dev/null
    )"

    if [[ "$LABEL_VALUE" == "$DIAGNOSTIC_LABEL" ]]; then
      docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
    fi
  fi

  if [[ -n "$VOLUME" ]] && docker volume inspect "$VOLUME" >/dev/null 2>&1; then
    LABEL_VALUE="$(
      docker volume inspect \
        --format '{{ index .Labels "com.inzozi.code.diagnostic" }}' \
        "$VOLUME" \
        2>/dev/null
    )"

    if [[ "$LABEL_VALUE" == "$DIAGNOSTIC_LABEL" ]]; then
      docker volume rm "$VOLUME" >/dev/null 2>&1 || true
    fi
  fi

  set -e
}

trap cleanup EXIT

[[ "${EUID}" -eq 0 ]] || fail "must-run-as-root"
[[ -d "${APP_ROOT}/.git" ]] || fail "missing-git-checkout"
command -v docker >/dev/null 2>&1 || fail "docker-unavailable"
command -v restic >/dev/null 2>&1 || fail "restic-unavailable"
command -v python3 >/dev/null 2>&1 || fail "python3-unavailable"

require_private_file "$RESTIC_ENV_FILE"

# shellcheck disable=SC1090
set -a
source "$RESTIC_ENV_FILE"
set +a

[[ -n "${RESTIC_REPOSITORY:-}" ]] || fail "restic-repository-unset"
[[ -n "${RESTIC_PASSWORD_FILE:-}" ]] || fail "restic-password-file-unset"
require_private_file "$RESTIC_PASSWORD_FILE"

docker image inspect "$POSTGRES_IMAGE" >/dev/null 2>&1 || fail "postgres-image-not-present"

SNAPSHOTS_JSON="$(
  restic snapshots \
    --tag postgres \
    --json
)"

SNAPSHOT_ID="$(
  printf '%s' "$SNAPSHOTS_JSON" |
  python3 -c '
import json
import sys
from datetime import datetime

rows = json.load(sys.stdin)
if not rows:
    raise SystemExit(1)

rows.sort(key=lambda row: row.get("time", ""))
value = rows[-1].get("id") or rows[-1].get("short_id")
if not value:
    raise SystemExit(1)

sys.stdout.write(str(value))
'
)" || fail "no-postgres-restic-snapshot"

unset SNAPSHOTS_JSON

LS_JSON="$(
  restic ls \
    --json \
    "$SNAPSHOT_ID"
)"

DUMP_PATH="$(
  printf '%s\n' "$LS_JSON" |
  python3 -c '
import json
import sys

paths = []

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue

    row = json.loads(line)
    if row.get("struct_type") != "node":
        continue

    path = row.get("path")
    if isinstance(path, str) and path.endswith(".dump"):
        paths.append(path)

if len(paths) != 1:
    raise SystemExit(1)

sys.stdout.write(paths[0])
'
)" || fail "snapshot-dump-path-not-unique"

unset LS_JSON

RUN_ID="$(date -u '+%Y%m%d%H%M%S')-$RANDOM"
CONTAINER="inzozi-issue39-restore-${RUN_ID}"
VOLUME="inzozi-issue39-restore-${RUN_ID}"

docker volume create \
  --label "com.inzozi.code.diagnostic=${DIAGNOSTIC_LABEL}" \
  "$VOLUME" \
  >/dev/null

docker run \
  -d \
  --name "$CONTAINER" \
  --label "com.inzozi.code.diagnostic=${DIAGNOSTIC_LABEL}" \
  --network none \
  --restart no \
  -e POSTGRES_DB=inzozi_recovery \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_HOST_AUTH_METHOD=trust \
  -v "${VOLUME}:/var/lib/postgresql/data" \
  "$POSTGRES_IMAGE" \
  >/dev/null

READY='NO'

for _ in $(seq 1 60); do
  if docker exec "$CONTAINER" \
    pg_isready \
      -U postgres \
      -d inzozi_recovery \
      >/dev/null 2>&1
  then
    READY='YES'
    break
  fi

  sleep 1
done

[[ "$READY" == 'YES' ]] || fail "diagnostic-postgres-not-ready"

printf '%s\n' 'ISSUE39_RESTORE_TEST_START=YES'
printf '%s\n' 'RESTORE_SOURCE=OFF_HOST_RESTIC'
printf '%s\n' 'RESTORE_TARGET=ISOLATED_DIAGNOSTIC_POSTGRES'
printf '%s\n' 'LIVE_DATABASE_TARGETED=NO'
printf '%s\n' 'DIAGNOSTIC_NETWORK=NONE'
printf '%s\n' 'SECRET_VALUES_PRINTED=NO'

set +e

restic dump "$SNAPSHOT_ID" "$DUMP_PATH" |
docker exec \
  -i \
  "$CONTAINER" \
  pg_restore \
    --exit-on-error \
    --no-owner \
    --no-privileges \
    -U postgres \
    -d inzozi_recovery

RESTORE_RC=$?
set -e

[[ "$RESTORE_RC" -eq 0 ]] || fail "restic-dump-or-pg-restore-failed"

VERIFY_RESULT="$(
  docker exec "$CONTAINER" \
    psql \
      -X \
      -A \
      -t \
      -v ON_ERROR_STOP=1 \
      -U postgres \
      -d inzozi_recovery \
      -c "
        SELECT 'AUTH_USERS_TABLE=' ||
          CASE WHEN to_regclass('public.auth_users') IS NOT NULL
            THEN 'PASS' ELSE 'FAIL' END;

        SELECT 'WORKSPACE_OWNERSHIP_TABLE=' ||
          CASE WHEN to_regclass('public.workspace_ownership') IS NOT NULL
            THEN 'PASS' ELSE 'FAIL' END;

        SELECT 'WORKSPACE_AUDIT_TABLE=' ||
          CASE WHEN to_regclass('public.workspace_access_audit') IS NOT NULL
            THEN 'PASS' ELSE 'FAIL' END;

        SELECT 'AUTH_USER_ROW_COUNT=' || COUNT(*)
          FROM auth_users;

        SELECT 'ACTIVE_IDENTITY_COUNT=' || COUNT(*)
          FROM auth_users
          WHERE status = 'active';

        SELECT 'ACTIVE_PLATFORM_OWNER_COUNT=' || COUNT(*)
          FROM auth_users
          WHERE status = 'active'
            AND role = 'platform_owner';

        SELECT 'PENDING_IDENTITY_COUNT=' || COUNT(*)
          FROM auth_users
          WHERE status = 'pending';

        SELECT 'WORKSPACE_OWNERSHIP_ROW_COUNT=' || COUNT(*)
          FROM workspace_ownership;

        SELECT 'WORKSPACE_AUDIT_ROW_COUNT=' || COUNT(*)
          FROM workspace_access_audit;
      " \
    </dev/null
)"

printf '%s\n' "$VERIFY_RESULT"

printf '%s\n' "$VERIFY_RESULT" | grep -Fxq 'AUTH_USERS_TABLE=PASS'
printf '%s\n' "$VERIFY_RESULT" | grep -Fxq 'WORKSPACE_OWNERSHIP_TABLE=PASS'
printf '%s\n' "$VERIFY_RESULT" | grep -Fxq 'WORKSPACE_AUDIT_TABLE=PASS'
printf '%s\n' "$VERIFY_RESULT" | grep -Fxq 'ACTIVE_IDENTITY_COUNT=1'
printf '%s\n' "$VERIFY_RESULT" | grep -Fxq 'ACTIVE_PLATFORM_OWNER_COUNT=1'
printf '%s\n' "$VERIFY_RESULT" | grep -Fxq 'PENDING_IDENTITY_COUNT=0'

unset VERIFY_RESULT

printf '%s\n' 'POSTGRES_RESTORE_STREAM=PASS'
printf '%s\n' 'RESTORED_SCHEMA_QUERY=PASS'
printf '%s\n' 'RESTORED_IDENTITY_INVARIANTS=PASS'
printf '%s\n' 'RESTORED_WORKSPACE_AUDIT_QUERY=PASS'
printf '%s\n' 'LIVE_DATABASE_MUTATION=NO'
printf '%s\n' 'LIVE_REDIS_MUTATION=NO'
printf '%s\n' 'LIVE_WORKSPACE_MUTATION=NO'
printf '%s\n' 'SECRET_VALUES_PRINTED=NO'
printf '%s\n' 'ISSUE39_ISOLATED_POSTGRES_RESTORE_TEST=PASS'
