#!/usr/bin/env bash
set -euo pipefail
umask 077

APP_ROOT="${APP_ROOT:-/srv/inzozi-code/application}"

GOTIFY_TOKEN_FILE="${GOTIFY_TOKEN_FILE:-/srv/inzozi-code/secrets/gotify-app-token}"
OPS_ALERT_CONFIG="${OPS_ALERT_CONFIG:-/srv/inzozi-code/secrets/ops-alert.curl.conf}"

EXPECTED_COMMIT_SHA="${EXPECTED_COMMIT_SHA:-}"
CONFIRM_GOTIFY_ADAPTER_INSTALL="${CONFIRM_GOTIFY_ADAPTER_INSTALL:-NO}"

SOURCE_ADAPTER="${APP_ROOT}/infrastructure/staging/gotify_alert_adapter.py"
SOURCE_UNIT="${APP_ROOT}/infrastructure/staging/systemd/inzozi-code-gotify-alert-adapter.service"
SOURCE_CURL_CONFIG="${APP_ROOT}/infrastructure/staging/ops-alert.curl.conf.gotify.example"

DEST_ADAPTER='/usr/local/sbin/inzozi-code-gotify-alert-adapter'
DEST_UNIT='/etc/systemd/system/inzozi-code-gotify-alert-adapter.service'

private_file_ok() {
  local path="$1"
  local mode owner

  [[ -f "$path" ]] || return 1
  [[ ! -L "$path" ]] || return 1

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

[[ "${EUID}" -eq 0 ]] || {
  echo 'ADAPTER_INSTALL_ERROR=must-run-as-root'
  exit 1
}

[[ "$CONFIRM_GOTIFY_ADAPTER_INSTALL" == 'YES' ]] || {
  echo 'GOTIFY_ADAPTER_INSTALL_AUTHORIZED=NO'
  exit 1
}

[[ "$EXPECTED_COMMIT_SHA" =~ ^[0-9a-f]{40}$ ]] || {
  echo 'EXPECTED_COMMIT_SHA_VALID=NO'
  exit 1
}

cd "$APP_ROOT"

[[ "$(git rev-parse HEAD)" == "$EXPECTED_COMMIT_SHA" ]] || {
  echo 'STAGING_EXACT_SHA=NO'
  exit 1
}

[[ -z "$(git status --porcelain)" ]] || {
  echo 'STAGING_CHECKOUT_CLEAN=NO'
  exit 1
}

echo 'STAGING_EXACT_SHA=PASS'
echo 'STAGING_CHECKOUT_CLEAN=YES'

for FILE in \
  "$SOURCE_ADAPTER" \
  "$SOURCE_UNIT" \
  "$SOURCE_CURL_CONFIG"
do
  [[ -f "$FILE" ]] || {
    echo "REVIEWED_FILE_MISSING=$FILE"
    exit 1
  }

  [[ ! -L "$FILE" ]] || {
    echo "REVIEWED_FILE_SYMLINK=$FILE"
    exit 1
  }
done

echo 'REVIEWED_ADAPTER_FILES_PRESENT=PASS'

for unit in \
  inzozi-code-postgres-backup.timer \
  inzozi-code-restic-check.timer \
  inzozi-code-ops-monitor.timer
do
  ! systemctl is-active --quiet "$unit" || {
    echo "UNEXPECTED_ACTIVE_TIMER=$unit"
    exit 1
  }
done

echo 'ISSUE39_TIMERS_ACTIVE=NO'

GOTIFY_HEALTH="$(
  curl \
    --silent \
    --noproxy '*' \
    --output /dev/null \
    --write-out '%{http_code}' \
    --connect-timeout 3 \
    --max-time 5 \
    http://127.0.0.1:8088/health \
    || true
)"

echo "GOTIFY_HEALTH_HTTP=$GOTIFY_HEALTH"

[[ "$GOTIFY_HEALTH" == '200' ]]

private_file_ok "$GOTIFY_TOKEN_FILE" || {
  echo 'GOTIFY_APP_TOKEN_PRIVATE=NO'
  exit 1
}

TOKEN_LENGTH="$(
  python3 - "$GOTIFY_TOKEN_FILE" <<'PY'
from pathlib import Path
import sys

token = Path(
    sys.argv[1]
).read_text(
    encoding="utf-8"
).strip()

if not token:
    raise SystemExit(1)

if len(token) > 512:
    raise SystemExit(1)

if any(
    character.isspace()
    for character in token
):
    raise SystemExit(1)

if any(
    ord(character) < 33
    or ord(character) > 126
    for character in token
):
    raise SystemExit(1)

print(len(token))
PY
)"

echo "GOTIFY_APP_TOKEN_LENGTH=$TOKEN_LENGTH"
echo 'GOTIFY_APP_TOKEN_VALUE_PRINTED=NO'
echo 'GOTIFY_APP_TOKEN_PRIVATE=PASS'

python3 <<'PY_RUNTIME'
import sys

if sys.version_info < (3, 10):
    raise SystemExit(
        "PYTHON_RUNTIME_COMPATIBLE=NO"
    )

print("PYTHON_RUNTIME_COMPATIBLE=PASS")
PY_RUNTIME

PORT_COUNT="$(
  ss -H -ltn |
  awk '
    $4 ~ /:8091$/ {n++}
    END {print n+0}
  '
)"

echo "LOCAL_PORT_8091_LISTENER_COUNT_BEFORE=$PORT_COUNT"

[[ "$PORT_COUNT" == '0' ]] || {
  echo 'LOCAL_PORT_8091_FREE=NO'
  exit 1
}

echo 'LOCAL_PORT_8091_FREE=YES'

for path in \
  "$DEST_ADAPTER" \
  "$DEST_UNIT" \
  "$OPS_ALERT_CONFIG"
do
  if [[ -e "$path" || -L "$path" ]]; then
    echo "GOTIFY_ADAPTER_DESTINATION_PREEXISTING=$path"
    exit 1
  fi
done

echo 'GOTIFY_ADAPTER_DESTINATIONS_AVAILABLE=PASS'

command -v systemd-analyze >/dev/null 2>&1 || {
  echo 'SYSTEMD_ANALYZE_AVAILABLE=NO'
  exit 1
}

ADAPTER_DESTINATION_ARMED='NO'
UNIT_DESTINATION_ARMED='NO'
SERVICE_START_ATTEMPTED='NO'
SERVICE_ENABLE_ATTEMPTED='NO'
SERVICE_ENABLED_BY_INSTALLER='NO'
ALERT_CONFIG_DESTINATION_ARMED='NO'
ALERT_CONFIG_CREATED='NO'

contain_failed_adapter_install() {
  local rc="$?"

  trap - EXIT

  if [[ "$rc" -ne 0 ]]; then
    if [[ "$SERVICE_ENABLE_ATTEMPTED" == 'YES' ]]; then
      systemctl disable \
        inzozi-code-gotify-alert-adapter.service \
        >/dev/null 2>&1 \
        || true
    fi

    if [[ "$SERVICE_START_ATTEMPTED" == 'YES' ]]; then
      systemctl stop \
        inzozi-code-gotify-alert-adapter.service \
        >/dev/null 2>&1 \
        || true
    fi

    if [[ "$ALERT_CONFIG_DESTINATION_ARMED" == 'YES' ]]; then
      rm -f -- \
        "$OPS_ALERT_CONFIG" \
        || true
    fi

    if [[ "$UNIT_DESTINATION_ARMED" == 'YES' ]]; then
      rm -f -- \
        "$DEST_UNIT" \
        || true

      systemctl daemon-reload \
        >/dev/null 2>&1 \
        || true
    fi

    if [[ "$ADAPTER_DESTINATION_ARMED" == 'YES' ]]; then
      rm -f -- \
        "$DEST_ADAPTER" \
        || true
    fi

    echo 'GOTIFY_ADAPTER_FAILURE_CONTAINMENT_ATTEMPTED=YES' >&2
    echo 'GOTIFY_ADAPTER_ROLLBACK_SCOPE=EXACT_NEW_DESTINATIONS_ONLY' >&2
  fi

  exit "$rc"
}

trap contain_failed_adapter_install EXIT

echo 'GOTIFY_ADAPTER_FAILURE_CONTAINMENT_ARMED=PASS'

ADAPTER_DESTINATION_ARMED='YES'

install \
  -o root \
  -g root \
  -m 0755 \
  "$SOURCE_ADAPTER" \
  "$DEST_ADAPTER"

echo 'GOTIFY_ADAPTER_EXECUTABLE_INSTALLED=PASS'

systemd-analyze verify \
  "$SOURCE_UNIT" \
  >/dev/null

echo 'GOTIFY_ADAPTER_SYSTEMD_VERIFY=PASS'

UNIT_DESTINATION_ARMED='YES'

install \
  -o root \
  -g root \
  -m 0644 \
  "$SOURCE_UNIT" \
  "$DEST_UNIT"

echo 'GOTIFY_ADAPTER_UNIT_INSTALLED=PASS'

systemctl daemon-reload

SERVICE_START_ATTEMPTED='YES'

systemctl start \
  inzozi-code-gotify-alert-adapter.service

echo 'GOTIFY_ADAPTER_SERVICE_STARTED=PASS'

HEALTH=''

for _ in $(seq 1 20); do
  HEALTH="$(
    curl \
      --silent \
      --noproxy '*' \
      --output /dev/null \
      --write-out '%{http_code}' \
      --connect-timeout 2 \
      --max-time 3 \
      http://127.0.0.1:8091/health \
      || true
  )"

  [[ "$HEALTH" == '200' ]] && break
  sleep 1
done

echo "GOTIFY_ADAPTER_HEALTH_HTTP=$HEALTH"

[[ "$HEALTH" == '200' ]] || {
  echo 'GOTIFY_ADAPTER_HEALTH=FAIL'
  exit 1
}

LISTENER="$(
  ss -H -ltn |
  awk '
    $4 ~ /:8091$/ {
      print $4
    }
  '
)"

LISTENER_COUNT="$(
  printf '%s\n' "$LISTENER" |
  sed '/^[[:space:]]*$/d' |
  wc -l |
  tr -d ' '
)"

[[ "$LISTENER_COUNT" == '1' ]] || {
  echo 'GOTIFY_ADAPTER_LISTENER_COUNT=FAIL'
  exit 1
}

[[ "$LISTENER" == '127.0.0.1:8091' ]] || {
  echo 'GOTIFY_ADAPTER_LOOPBACK_BIND=FAIL'
  exit 1
}

echo 'GOTIFY_ADAPTER_LOOPBACK_BIND=PASS'
echo 'GOTIFY_ADAPTER_RUNTIME_PROOF=PASS'

SERVICE_ENABLE_ATTEMPTED='YES'

systemctl enable \
  inzozi-code-gotify-alert-adapter.service \
  >/dev/null

SERVICE_ENABLED_BY_INSTALLER='YES'

systemctl is-active --quiet \
  inzozi-code-gotify-alert-adapter.service

[[ "$(
  systemctl is-enabled \
    inzozi-code-gotify-alert-adapter.service
)" == 'enabled' ]]

echo 'GOTIFY_ADAPTER_BOOT_PERSISTENCE=PASS'

ALERT_CONFIG_DESTINATION_ARMED='YES'

install \
  -o root \
  -g root \
  -m 0600 \
  "$SOURCE_CURL_CONFIG" \
  "$OPS_ALERT_CONFIG"

ALERT_CONFIG_CREATED='YES'

[[ ! -L "$OPS_ALERT_CONFIG" ]]
[[ "$(stat -c '%u' "$OPS_ALERT_CONFIG")" == '0' ]]
[[ "$(stat -c '%a' "$OPS_ALERT_CONFIG")" == '600' ]]

echo 'OPS_ALERT_CONFIG_ROOT_PRIVATE=PASS'

trap - EXIT

ADAPTER_DESTINATION_ARMED='NO'
UNIT_DESTINATION_ARMED='NO'
SERVICE_START_ATTEMPTED='NO'
SERVICE_ENABLE_ATTEMPTED='NO'
SERVICE_ENABLED_BY_INSTALLER='NO'
ALERT_CONFIG_DESTINATION_ARMED='NO'
ALERT_CONFIG_CREATED='NO'

echo 'GOTIFY_ADAPTER_FAILURE_CONTAINMENT_ARMED=NO'

for unit in \
  inzozi-code-postgres-backup.timer \
  inzozi-code-restic-check.timer \
  inzozi-code-ops-monitor.timer
do
  ! systemctl is-active --quiet "$unit" || {
    echo "UNEXPECTED_ACTIVE_TIMER_AFTER=$unit"
    exit 1
  }
done

echo 'ISSUE39_TIMERS_ACTIVE_AFTER=NO'
echo 'SYNTHETIC_ALERT_SENT=NO'
echo 'MONITOR_RUN=NO'
echo 'FIREWALL_CHANGE=NO'
echo 'DNS_CHANGE=NO'
echo 'PRODUCTION_CHANGE=NO'

echo '============================================================'
echo 'ISSUE39_GOTIFY_ALERT_ADAPTER_INSTALL=PASS'
echo 'NEXT_STEP=SYNTHETIC_ALERT_DELIVERY_PROOF'
echo '============================================================'
