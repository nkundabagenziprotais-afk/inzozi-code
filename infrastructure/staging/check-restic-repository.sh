#!/usr/bin/env bash
set -euo pipefail
umask 077

RESTIC_ENV_FILE="${RESTIC_ENV_FILE:-/srv/inzozi-code/secrets/restic.env}"
LOCK_FILE="${LOCK_FILE:-/run/lock/inzozi-code-restic-check.lock}"

fail() {
  printf 'RESTIC_CHECK_ERROR=%s\n' "$1" >&2
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
  fail "restic-check-already-running"
fi

printf '%s\n' 'ISSUE39_RESTIC_CHECK_START=YES'
printf '%s\n' 'RESTIC_SECRET_VALUES_PRINTED=NO'

if ! restic check --quiet; then
  fail "restic-integrity-check-failed"
fi

printf '%s\n' 'RESTIC_REPOSITORY_INTEGRITY=PASS'
printf '%s\n' 'RESTIC_SECRET_VALUES_PRINTED=NO'
printf '%s\n' 'ISSUE39_RESTIC_CHECK=PASS'
