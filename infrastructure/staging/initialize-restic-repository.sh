#!/usr/bin/env bash
set -euo pipefail
umask 077

RESTIC_ENV_FILE="${RESTIC_ENV_FILE:-/srv/inzozi-code/secrets/restic.env}"
CONFIRM_RESTIC_INIT="${CONFIRM_RESTIC_INIT:-NO}"

fail() {
  printf 'RESTIC_INIT_ERROR=%s\n' "$1" >&2
  exit 1
}

require_private_file() {
  local path="$1"
  local mode owner

  [[ -f "$path" ]] || fail "missing-required-secret-file"
  mode="$(stat -c '%a' "$path")"
  owner="$(stat -c '%u' "$path")"
  [[ "$owner" == '0' ]] || fail "secret-file-owner-must-be-root"

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

require_private_file "$RESTIC_ENV_FILE"

# shellcheck disable=SC1090
set -a
source "$RESTIC_ENV_FILE"
set +a

[[ -n "${RESTIC_REPOSITORY:-}" ]] || fail "restic-repository-unset"
[[ -n "${RESTIC_PASSWORD_FILE:-}" ]] || fail "restic-password-file-unset"
require_private_file "$RESTIC_PASSWORD_FILE"

if restic cat config >/dev/null 2>&1; then
  printf '%s\n' 'RESTIC_REPOSITORY_ALREADY_INITIALIZED=YES'
  printf '%s\n' 'RESTIC_REPOSITORY_INITIALIZED_THIS_STEP=NO'
  printf '%s\n' 'RESTIC_SECRET_VALUES_PRINTED=NO'
  printf '%s\n' 'ISSUE39_RESTIC_REPOSITORY_READY=PASS'
  exit 0
fi

[[ "$CONFIRM_RESTIC_INIT" == 'YES' ]] || fail "repository-not-readable-confirm-explicit-init-after-verifying-backend-target"

if ! restic init; then
  fail "restic-init-failed"
fi

restic cat config >/dev/null 2>&1 || fail "restic-config-unreadable-after-init"

printf '%s\n' 'RESTIC_REPOSITORY_ALREADY_INITIALIZED=NO'
printf '%s\n' 'RESTIC_REPOSITORY_INITIALIZED_THIS_STEP=PASS'
printf '%s\n' 'RESTIC_SECRET_VALUES_PRINTED=NO'
printf '%s\n' 'ISSUE39_RESTIC_REPOSITORY_READY=PASS'
