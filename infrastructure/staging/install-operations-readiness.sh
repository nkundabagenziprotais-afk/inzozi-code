#!/usr/bin/env bash
set -euo pipefail
umask 077

APP_ROOT="${APP_ROOT:-/srv/inzozi-code/application}"
EXPECTED_COMMIT_SHA="${EXPECTED_COMMIT_SHA:-}"
INSTALL_RESTIC="${INSTALL_RESTIC:-NO}"
ACTIVATE_TIMERS="${ACTIVATE_TIMERS:-NO}"
RESTIC_ENV_FILE="${RESTIC_ENV_FILE:-/srv/inzozi-code/secrets/restic.env}"
ALERT_CURL_CONFIG_FILE="${ALERT_CURL_CONFIG_FILE:-/srv/inzozi-code/secrets/ops-alert.curl.conf}"
BACKUP_STATE_FILE="${BACKUP_STATE_FILE:-/srv/inzozi-code/backups/postgres-last-success}"
SYSTEMD_SOURCE="${APP_ROOT}/infrastructure/staging/systemd"

fail() {
  printf 'OPS_INSTALL_ERROR=%s\n' "$1" >&2
  exit 1
}

private_file_ok() {
  local path="$1"
  local mode owner

  [[ -f "$path" ]] || return 1
  mode="$(stat -c '%a' "$path")"
  owner="$(stat -c '%u' "$path")"
  [[ "$owner" == '0' ]] || return 1

  case "$mode" in
    400|600)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

[[ "${EUID}" -eq 0 ]] || fail "must-run-as-root"
[[ "$EXPECTED_COMMIT_SHA" =~ ^[0-9a-f]{40}$ ]] || fail "expected-commit-sha-required"
[[ -d "${APP_ROOT}/.git" ]] || fail "missing-git-checkout"

cd "$APP_ROOT"

ACTUAL_SHA="$(git rev-parse HEAD)"
[[ "$ACTUAL_SHA" == "$EXPECTED_COMMIT_SHA" ]] || fail "checkout-sha-mismatch"
[[ -z "$(git status --porcelain)" ]] || fail "checkout-dirty"

for path in \
  infrastructure/staging/initialize-restic-repository.sh \
  infrastructure/staging/backup-postgres-restic.sh \
  infrastructure/staging/check-restic-repository.sh \
  infrastructure/staging/restore-postgres-restic-test.sh \
  infrastructure/staging/monitor-operations.sh \
  infrastructure/staging/systemd/inzozi-code-postgres-backup.service \
  infrastructure/staging/systemd/inzozi-code-postgres-backup.timer \
  infrastructure/staging/systemd/inzozi-code-restic-check.service \
  infrastructure/staging/systemd/inzozi-code-restic-check.timer \
  infrastructure/staging/systemd/inzozi-code-ops-monitor.service \
  infrastructure/staging/systemd/inzozi-code-ops-monitor.timer
do
  [[ -f "$path" ]] || fail "reviewed-operations-file-missing"
done

if ! command -v restic >/dev/null 2>&1; then
  if [[ "$INSTALL_RESTIC" != 'YES' ]]; then
    fail "restic-unavailable-set-install-restic-yes-for-explicit-package-install"
  fi

  command -v apt-get >/dev/null 2>&1 || fail "apt-get-unavailable"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq restic
fi

command -v restic >/dev/null 2>&1 || fail "restic-install-failed"

install -d -m 0700 /srv/inzozi-code/secrets
install -d -m 0700 /srv/inzozi-code/backups

install -m 0755 \
  infrastructure/staging/initialize-restic-repository.sh \
  /usr/local/sbin/inzozi-code-restic-init

install -m 0755 \
  infrastructure/staging/backup-postgres-restic.sh \
  /usr/local/sbin/inzozi-code-postgres-backup

install -m 0755 \
  infrastructure/staging/check-restic-repository.sh \
  /usr/local/sbin/inzozi-code-restic-check

install -m 0755 \
  infrastructure/staging/restore-postgres-restic-test.sh \
  /usr/local/sbin/inzozi-code-postgres-restore-test

install -m 0755 \
  infrastructure/staging/monitor-operations.sh \
  /usr/local/sbin/inzozi-code-ops-monitor

for unit in \
  inzozi-code-postgres-backup.service \
  inzozi-code-postgres-backup.timer \
  inzozi-code-restic-check.service \
  inzozi-code-restic-check.timer \
  inzozi-code-ops-monitor.service \
  inzozi-code-ops-monitor.timer
do
  install -m 0644 \
    "${SYSTEMD_SOURCE}/${unit}" \
    "/etc/systemd/system/${unit}"
done

systemctl daemon-reload

for unit in \
  inzozi-code-postgres-backup.service \
  inzozi-code-postgres-backup.timer \
  inzozi-code-restic-check.service \
  inzozi-code-restic-check.timer \
  inzozi-code-ops-monitor.service \
  inzozi-code-ops-monitor.timer
do
  systemd-analyze verify "/etc/systemd/system/${unit}" >/dev/null
done

printf '%s\n' 'ISSUE39_OPERATIONS_INSTALL=PASS'
printf '%s\n' 'REVIEWED_SHA_BOUNDARY=PASS'
printf '%s\n' 'RESTIC_AVAILABLE=PASS'
printf '%s\n' 'OPERATIONS_SCRIPTS_INSTALLED=PASS'
printf '%s\n' 'SYSTEMD_UNITS_INSTALLED=PASS'
printf '%s\n' 'SYSTEMD_TIMERS_ACTIVATED=NO'
printf '%s\n' 'SECRET_VALUES_PRINTED=NO'

if [[ "$ACTIVATE_TIMERS" != 'YES' ]]; then
  printf '%s\n' 'NEXT_STEP=CONFIGURE_BACKUP_AND_ALERT_SECRETS_RUN_INITIAL_BACKUP_RESTORE_TEST_THEN_ACTIVATE'
  exit 0
fi

private_file_ok "$RESTIC_ENV_FILE" || fail "restic-env-missing-or-unsafe"
private_file_ok "$ALERT_CURL_CONFIG_FILE" || fail "alert-curl-config-missing-or-unsafe"
private_file_ok "$BACKUP_STATE_FILE" || fail "backup-freshness-marker-missing-or-unsafe"

LAST_BACKUP_EPOCH="$(tr -d '[:space:]' < "$BACKUP_STATE_FILE")"
NOW_EPOCH="$(date +%s)"

[[ "$LAST_BACKUP_EPOCH" =~ ^[0-9]+$ ]] || fail "backup-freshness-marker-invalid"
(( LAST_BACKUP_EPOCH <= NOW_EPOCH )) || fail "backup-freshness-marker-future"
(( NOW_EPOCH - LAST_BACKUP_EPOCH <= 25200 )) || fail "backup-older-than-seven-hours"

systemctl enable --now \
  inzozi-code-postgres-backup.timer \
  inzozi-code-restic-check.timer \
  inzozi-code-ops-monitor.timer

systemctl is-active --quiet inzozi-code-postgres-backup.timer || fail "backup-timer-not-active"
systemctl is-active --quiet inzozi-code-restic-check.timer || fail "restic-check-timer-not-active"
systemctl is-active --quiet inzozi-code-ops-monitor.timer || fail "ops-monitor-timer-not-active"

printf '%s\n' 'SYSTEMD_TIMERS_ACTIVATED=PASS'
printf '%s\n' 'BACKUP_FRESHNESS_PRE_ACTIVATION=PASS'
printf '%s\n' 'ALERT_DELIVERY_CONFIGURATION_PRESENT=PASS'
printf '%s\n' 'ISSUE39_OPERATIONS_ACTIVATION=PASS'
