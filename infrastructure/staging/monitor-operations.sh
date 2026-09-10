#!/usr/bin/env bash
set -euo pipefail
umask 077

APP_ROOT="${APP_ROOT:-/srv/inzozi-code/application}"
ENV_FILE="${ENV_FILE:-/srv/inzozi-code/.env.staging}"
DOMAIN="${DOMAIN:-code-staging.inzozidigital.com}"
WORKSPACE_ROOT="${WORKSPACE_ROOT:-/srv/inzozi-code/workspace-data}"
BACKUP_STATE_FILE="${BACKUP_STATE_FILE:-/srv/inzozi-code/backups/postgres-last-success}"
ALERT_CURL_CONFIG_FILE="${ALERT_CURL_CONFIG_FILE:-/srv/inzozi-code/secrets/ops-alert.curl.conf}"
ACCESS_LOG="${ACCESS_LOG:-/var/log/nginx/access.log}"
STAGING_COMPOSE="${APP_ROOT}/infrastructure/staging/docker-compose.staging.yml"
CA_CERT="${CA_CERT:-/etc/inzozi-code/tls/${DOMAIN}/ca.pem}"
CERT_FILE="${CERT_FILE:-/etc/inzozi-code/tls/${DOMAIN}/cert.pem}"
EXPECT_ZERO_WORKSPACES="${EXPECT_ZERO_WORKSPACES:-NO}"
FIVE_XX_WINDOW_SECONDS="${FIVE_XX_WINDOW_SECONDS:-300}"
FIVE_XX_CRITICAL_THRESHOLD="${FIVE_XX_CRITICAL_THRESHOLD:-10}"

WARNINGS=()
CRITICALS=()

log() {
  printf '%s\n' "$1"
}

warning() {
  WARNINGS+=("$1")
}

critical() {
  CRITICALS+=("$1")
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

container_health() {
  local service="$1"
  local marker="$2"
  local cid health

  cid="$(
    "${COMPOSE[@]}" ps -q "$service" </dev/null 2>/dev/null || true
  )"

  if [[ -z "$cid" ]]; then
    critical "${marker}_MISSING"
    return
  fi

  health="$(
    docker inspect \
      --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{if .State.Running}}running{{else}}stopped{{end}}{{end}}' \
      "$cid" \
      </dev/null 2>/dev/null || true
  )"

  case "$health" in
    healthy|running)
      log "${marker}=PASS"
      ;;
    *)
      critical "${marker}_UNHEALTHY"
      ;;
  esac
}

deliver_alert() {
  local severity="$1"
  local summary="$2"
  local payload

  if ! private_file_ok "$ALERT_CURL_CONFIG_FILE"; then
    log 'ALERT_DELIVERY=UNCONFIGURED_OR_UNSAFE'
    return 1
  fi

  payload="$(
    python3 - \
      "$severity" \
      "$summary" <<'PY'
import json
import sys
from datetime import datetime, timezone

severity = sys.argv[1]
summary = sys.argv[2]

print(json.dumps({
    "service": "inzozi-code-staging",
    "severity": severity,
    "summary": summary,
    "timestamp": datetime.now(timezone.utc).isoformat(),
}, separators=(",", ":")))
PY
  )"

  if printf '%s' "$payload" |
    curl \
      --silent \
      --show-error \
      --fail \
      --connect-timeout 5 \
      --max-time 15 \
      --config "$ALERT_CURL_CONFIG_FILE" \
      --header 'Content-Type: application/json' \
      --data-binary @- \
      >/dev/null
  then
    log 'ALERT_DELIVERY=PASS'
    return 0
  fi

  log 'ALERT_DELIVERY=FAIL'
  return 1
}

[[ "${EUID}" -eq 0 ]] || {
  printf '%s\n' 'MONITOR_ERROR=must-run-as-root' >&2
  exit 1
}

for tool in docker curl python3 nginx findmnt; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'MONITOR_ERROR=missing-tool-%s\n' "$tool" >&2
    exit 1
  }
done

[[ -d "${APP_ROOT}/.git" ]] || {
  printf '%s\n' 'MONITOR_ERROR=missing-git-checkout' >&2
  exit 1
}
[[ -f "$ENV_FILE" ]] || {
  printf '%s\n' 'MONITOR_ERROR=missing-staging-env' >&2
  exit 1
}
[[ "$(stat -c '%a' "$ENV_FILE")" == '600' ]] || {
  printf '%s\n' 'MONITOR_ERROR=staging-env-mode' >&2
  exit 1
}
[[ -f "$STAGING_COMPOSE" ]] || {
  printf '%s\n' 'MONITOR_ERROR=missing-staging-compose' >&2
  exit 1
}

cd "$APP_ROOT"

COMPOSE=(
  docker compose
  --env-file "$ENV_FILE"
  -f docker-compose.yml
  -f "$STAGING_COMPOSE"
)

log 'ISSUE39_OPS_MONITOR_START=YES'
log 'MONITOR_SECRET_VALUES_PRINTED=NO'

if [[ -n "$(git status --porcelain)" ]]; then
  critical 'STAGING_CHECKOUT_DIRTY'
else
  log 'STAGING_CHECKOUT_CLEAN=PASS'
fi

if [[ -f "$CA_CERT" ]] && [[ -f "$CERT_FILE" ]]; then
  HEALTH_HTTP="$(
    curl \
      --silent \
      --show-error \
      --noproxy '*' \
      --cacert "$CA_CERT" \
      --resolve "${DOMAIN}:443:127.0.0.1" \
      --connect-timeout 5 \
      --max-time 10 \
      --output /dev/null \
      --write-out '%{http_code}' \
      "https://${DOMAIN}/health" \
      2>/dev/null || true
  )"

  READY_HTTP="$(
    curl \
      --silent \
      --show-error \
      --noproxy '*' \
      --cacert "$CA_CERT" \
      --resolve "${DOMAIN}:443:127.0.0.1" \
      --connect-timeout 5 \
      --max-time 10 \
      --output /dev/null \
      --write-out '%{http_code}' \
      "https://${DOMAIN}/ready" \
      2>/dev/null || true
  )"

  if [[ "$HEALTH_HTTP" == '200' ]]; then
    log 'HTTPS_HEALTH=PASS'
  else
    critical 'HTTPS_HEALTH_FAIL'
  fi

  if [[ "$READY_HTTP" == '200' ]]; then
    log 'HTTPS_READY=PASS'
  else
    critical 'HTTPS_READY_FAIL'
  fi
else
  critical 'PRIVATE_TLS_FILES_MISSING'
fi

container_health postgres POSTGRES_CONTAINER
container_health redis REDIS_CONTAINER
container_health workspace-manager WORKSPACE_MANAGER_CONTAINER
container_health workspace-broker WORKSPACE_BROKER_CONTAINER
container_health workspace-egress-proxy WORKSPACE_EGRESS_PROXY_CONTAINER

if systemctl is-active --quiet nginx 2>/dev/null; then
  log 'HOST_NGINX_ACTIVE=PASS'
else
  critical 'HOST_NGINX_INACTIVE'
fi

if nginx -t >/dev/null 2>&1; then
  log 'HOST_NGINX_CONFIG=PASS'
else
  critical 'HOST_NGINX_CONFIG_FAIL'
fi

ROOT_USE="$(
  df -P / |
  awk 'NR == 2 {gsub(/%/,"",$5); print $5}'
)"

if [[ "$ROOT_USE" =~ ^[0-9]+$ ]]; then
  if (( ROOT_USE >= 90 )); then
    critical 'ROOT_DISK_GE_90'
  elif (( ROOT_USE >= 80 )); then
    warning 'ROOT_DISK_GE_80'
  else
    log 'ROOT_DISK_PRESSURE=PASS'
  fi
else
  critical 'ROOT_DISK_METRIC_INVALID'
fi

if mountpoint -q "$WORKSPACE_ROOT"; then
  WORKSPACE_USE="$(
    df -P "$WORKSPACE_ROOT" |
    awk 'NR == 2 {gsub(/%/,"",$5); print $5}'
  )"

  if [[ "$WORKSPACE_USE" =~ ^[0-9]+$ ]]; then
    if (( WORKSPACE_USE >= 90 )); then
      critical 'WORKSPACE_POOL_GE_90'
    elif (( WORKSPACE_USE >= 80 )); then
      warning 'WORKSPACE_POOL_GE_80'
    else
      log 'WORKSPACE_POOL_PRESSURE=PASS'
    fi
  else
    critical 'WORKSPACE_POOL_METRIC_INVALID'
  fi

  MOUNT_OPTIONS="$(findmnt -no OPTIONS "$WORKSPACE_ROOT" 2>/dev/null || true)"
  case ",$MOUNT_OPTIONS," in
    *,prjquota,*|*,pquota,*)
      log 'WORKSPACE_PROJECT_QUOTA_MOUNT=PASS'
      ;;
    *)
      critical 'WORKSPACE_PROJECT_QUOTA_MOUNT_MISSING'
      ;;
  esac
else
  critical 'WORKSPACE_POOL_NOT_MOUNTED'
fi

if private_file_ok "$BACKUP_STATE_FILE"; then
  LAST_BACKUP_EPOCH="$(tr -d '[:space:]' < "$BACKUP_STATE_FILE")"
  NOW_EPOCH="$(date +%s)"

  if [[ "$LAST_BACKUP_EPOCH" =~ ^[0-9]+$ ]] && (( LAST_BACKUP_EPOCH <= NOW_EPOCH )); then
    BACKUP_AGE_SECONDS="$((NOW_EPOCH - LAST_BACKUP_EPOCH))"

    if (( BACKUP_AGE_SECONDS > 25200 )); then
      critical 'POSTGRES_BACKUP_OLDER_THAN_7H'
    elif (( BACKUP_AGE_SECONDS > 21600 )); then
      warning 'POSTGRES_BACKUP_OLDER_THAN_6H'
    else
      log 'POSTGRES_BACKUP_FRESHNESS=PASS'
    fi
  else
    critical 'POSTGRES_BACKUP_MARKER_INVALID'
  fi
else
  critical 'POSTGRES_BACKUP_MARKER_MISSING_OR_UNSAFE'
fi

if [[ -f "$CERT_FILE" ]]; then
  CERT_END="$(
    openssl x509 \
      -in "$CERT_FILE" \
      -noout \
      -enddate \
      2>/dev/null |
    cut -d= -f2- || true
  )"

  if [[ -n "$CERT_END" ]]; then
    CERT_END_EPOCH="$(date -d "$CERT_END" +%s 2>/dev/null || true)"
    NOW_EPOCH="$(date +%s)"

    if [[ "$CERT_END_EPOCH" =~ ^[0-9]+$ ]]; then
      TLS_DAYS_REMAINING="$(( (CERT_END_EPOCH - NOW_EPOCH) / 86400 ))"

      if (( TLS_DAYS_REMAINING <= 7 )); then
        critical 'TLS_EXPIRY_LE_7D'
      elif (( TLS_DAYS_REMAINING <= 14 )); then
        warning 'TLS_EXPIRY_LE_14D'
      else
        log 'TLS_EXPIRY=PASS'
      fi
    else
      critical 'TLS_EXPIRY_PARSE_FAIL'
    fi
  else
    critical 'TLS_CERT_READ_FAIL'
  fi
fi

if [[ -f "$ACCESS_LOG" ]]; then
  FIVE_XX_COUNT="$(
    tail -n 5000 "$ACCESS_LOG" 2>/dev/null |
    python3 - "$FIVE_XX_WINDOW_SECONDS" <<'PY'
import re
import sys
from datetime import datetime, timezone

window = int(sys.argv[1])
now = datetime.now(timezone.utc)
pattern = re.compile(
    r'\[(?P<ts>[^\]]+)\]\s+"[^"]*"\s+(?P<status>\d{3})\s'
)
count = 0

for line in sys.stdin:
    match = pattern.search(line)
    if not match:
        continue

    try:
        timestamp = datetime.strptime(
            match.group("ts"),
            "%d/%b/%Y:%H:%M:%S %z",
        )
        status = int(match.group("status"))
    except (TypeError, ValueError):
        continue

    age = (now - timestamp.astimezone(timezone.utc)).total_seconds()
    if 0 <= age <= window and 500 <= status <= 599:
        count += 1

print(count)
PY
  )"

  if [[ "$FIVE_XX_COUNT" =~ ^[0-9]+$ ]]; then
    if (( FIVE_XX_COUNT >= FIVE_XX_CRITICAL_THRESHOLD )); then
      critical 'HTTP_5XX_THRESHOLD_EXCEEDED'
    else
      log 'HTTP_5XX_ROLLING_WINDOW=PASS'
    fi
  else
    critical 'HTTP_5XX_METRIC_INVALID'
  fi
else
  warning 'NGINX_ACCESS_LOG_MISSING'
fi

if [[ "$EXPECT_ZERO_WORKSPACES" == 'YES' ]]; then
  RUNTIME_COUNT="$(
    docker ps -a \
      --filter 'label=com.inzozi.code.workspace=true' \
      --format '{{.ID}}' \
      </dev/null |
    wc -l |
    tr -d ' '
  )"

  if [[ "$RUNTIME_COUNT" == '0' ]]; then
    log 'EXPECTED_ZERO_WORKSPACE_RESIDUE=PASS'
  else
    critical 'UNEXPECTED_WORKSPACE_RUNTIME_RESIDUE'
  fi
fi

WARNING_COUNT="${#WARNINGS[@]}"
CRITICAL_COUNT="${#CRITICALS[@]}"

log "MONITOR_WARNING_COUNT=${WARNING_COUNT}"
log "MONITOR_CRITICAL_COUNT=${CRITICAL_COUNT}"

if (( CRITICAL_COUNT == 0 && WARNING_COUNT == 0 )); then
  log 'ALERT_DELIVERY=NOT_REQUIRED'
  log 'MONITOR_SECRET_VALUES_PRINTED=NO'
  log 'ISSUE39_OPS_MONITOR=PASS'
  exit 0
fi

if (( CRITICAL_COUNT > 0 )); then
  SUMMARY="$(IFS=,; printf '%s' "${CRITICALS[*]}")"
  [[ "$WARNING_COUNT" -eq 0 ]] || SUMMARY+=";WARN:$(IFS=,; printf '%s' "${WARNINGS[*]}")"

  if ! deliver_alert 'critical' "$SUMMARY"; then
    log 'LOCAL_ALERT_MARKER=CRITICAL'
  fi

  log 'MONITOR_SECRET_VALUES_PRINTED=NO'
  log 'ISSUE39_OPS_MONITOR=CRITICAL'
  exit 2
fi

SUMMARY="$(IFS=,; printf '%s' "${WARNINGS[*]}")"

if ! deliver_alert 'warning' "$SUMMARY"; then
  log 'LOCAL_ALERT_MARKER=WARNING'
fi

log 'MONITOR_SECRET_VALUES_PRINTED=NO'
log 'ISSUE39_OPS_MONITOR=WARNING'
exit 1
