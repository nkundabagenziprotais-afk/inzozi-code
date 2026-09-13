#!/usr/bin/env bash
set -euo pipefail
umask 077

APP_ROOT="${APP_ROOT:-/srv/inzozi-code/application}"
ENV_FILE="${ENV_FILE:-/srv/inzozi-code/.env.staging}"

GOTIFY_ENV_FILE="${GOTIFY_ENV_FILE:-/srv/inzozi-code/secrets/gotify.env}"
GOTIFY_ADMIN_PASSWORD_FILE="${GOTIFY_ADMIN_PASSWORD_FILE:-/srv/inzozi-code/secrets/gotify-admin-password}"
GOTIFY_DATA_DIR="${GOTIFY_DATA_DIR:-/srv/inzozi-code/gotify-data}"

GOTIFY_COMPOSE="${APP_ROOT}/infrastructure/staging/docker-compose.gotify.yml"
STAGING_COMPOSE="${APP_ROOT}/infrastructure/staging/docker-compose.staging.yml"

EXPECTED_COMMIT_SHA="${EXPECTED_COMMIT_SHA:-}"
CONFIRM_GOTIFY_SERVER_INSTALL="${CONFIRM_GOTIFY_SERVER_INSTALL:-NO}"

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
  echo 'GOTIFY_INSTALL_ERROR=must-run-as-root'
  exit 1
}

[[ "$CONFIRM_GOTIFY_SERVER_INSTALL" == 'YES' ]] || {
  echo 'GOTIFY_SERVER_INSTALL_AUTHORIZED=NO'
  exit 1
}

[[ "$EXPECTED_COMMIT_SHA" =~ ^[0-9a-f]{40}$ ]] || {
  echo 'EXPECTED_COMMIT_SHA_VALID=NO'
  exit 1
}

for tool in \
  git docker curl python3 stat ss find
do
  command -v "$tool" >/dev/null 2>&1 || {
    echo "MISSING_TOOL=$tool"
    exit 1
  }
done

[[ -f "$ENV_FILE" ]] || {
  echo 'STAGING_ENV_FOUND=NO'
  exit 1
}

[[ ! -L "$ENV_FILE" ]] || {
  echo 'STAGING_ENV_SYMLINK=FAIL'
  exit 1
}

[[ "$(stat -c '%a' "$ENV_FILE")" == '600' ]] || {
  echo 'STAGING_ENV_MODE=FAIL'
  exit 1
}

[[ -f "$APP_ROOT/docker-compose.yml" ]] || {
  echo 'BASE_COMPOSE_FOUND=NO'
  exit 1
}

[[ -f "$STAGING_COMPOSE" ]] || {
  echo 'STAGING_COMPOSE_FOUND=NO'
  exit 1
}

[[ -f "$GOTIFY_COMPOSE" ]] || {
  echo 'GOTIFY_COMPOSE_FOUND=NO'
  exit 1
}

echo 'STAGING_ENV_FOUND=PASS'
echo 'STAGING_ENV_MODE=PASS'
echo 'REQUIRED_COMPOSE_FILES_PRESENT=PASS'

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

for unit in \
  inzozi-code-postgres-backup.timer \
  inzozi-code-restic-check.timer \
  inzozi-code-ops-monitor.timer
do
  if systemctl is-active --quiet "$unit"; then
    echo "UNEXPECTED_ACTIVE_TIMER=$unit"
    exit 1
  fi
done

echo 'ISSUE39_TIMERS_ACTIVE=NO'

private_file_ok "$GOTIFY_ENV_FILE" || {
  echo 'GOTIFY_ENV_PRIVATE=NO'
  exit 1
}

private_file_ok "$GOTIFY_ADMIN_PASSWORD_FILE" || {
  echo 'GOTIFY_ADMIN_PASSWORD_PRIVATE=NO'
  exit 1
}

PASSWORD_BYTES="$(
  wc -c < "$GOTIFY_ADMIN_PASSWORD_FILE" |
  tr -d ' '
)"

if (
  ((
    PASSWORD_BYTES < 16
    || PASSWORD_BYTES > 1024
  ))
); then
  echo 'GOTIFY_ADMIN_PASSWORD_LENGTH_VALID=NO'
  exit 1
fi

echo 'GOTIFY_ADMIN_PASSWORD_PRIVATE=PASS'
echo 'GOTIFY_ADMIN_PASSWORD_VALUE_PRINTED=NO'

GOTIFY_IMAGE="$(
  awk '
    /^GOTIFY_IMAGE=/ {
      sub(/^GOTIFY_IMAGE=/, "")
      print
      exit
    }
  ' "$GOTIFY_ENV_FILE"
)"

if [[ ! "$GOTIFY_IMAGE" =~ ^gotify/server:3\.1\.0@sha256:[0-9a-f]{64}$ ]]; then
  echo 'GOTIFY_IMAGE_IMMUTABLE_PIN=FAIL'
  exit 1
fi

echo 'GOTIFY_VERSION_PIN=3.1.0'
echo 'GOTIFY_IMAGE_IMMUTABLE_PIN=PASS'

PORT_COUNT="$(
  ss -H -ltn |
  awk '
    $4 ~ /:8088$/ {n++}
    END {print n+0}
  '
)"

echo "LOCAL_PORT_8088_LISTENER_COUNT=$PORT_COUNT"

[[ "$PORT_COUNT" == '0' ]] || {
  echo 'LOCAL_PORT_8088_FREE=NO'
  exit 1
}

echo 'LOCAL_PORT_8088_FREE=YES'

GOTIFY_CONTAINER_COUNT="$(
  docker ps -a \
    --format '{{.Names}}' |
  awk '
    BEGIN {IGNORECASE=1}
    /(^|[-_])gotify($|[-_])/ {n++}
    END {print n+0}
  '
)"

echo "GOTIFY_CONTAINER_COUNT_BEFORE=$GOTIFY_CONTAINER_COUNT"

[[ "$GOTIFY_CONTAINER_COUNT" == '0' ]] || {
  echo 'GOTIFY_CONTAINER_PREEXISTING=YES'
  exit 1
}

if [[ -L "$GOTIFY_DATA_DIR" ]]; then
  echo 'GOTIFY_DATA_DIRECTORY_SYMLINK=FAIL'
  exit 1
fi

if [[ -e "$GOTIFY_DATA_DIR" ]]; then
  [[ -d "$GOTIFY_DATA_DIR" ]]
  [[ "$(stat -c '%u' "$GOTIFY_DATA_DIR")" == '0' ]]
  [[ "$(stat -c '%a' "$GOTIFY_DATA_DIR")" == '700' ]]

  DATA_ENTRY="$(
    find "$GOTIFY_DATA_DIR" \
      -mindepth 1 \
      -maxdepth 1 \
      -print \
      -quit
  )"

  [[ -z "$DATA_ENTRY" ]] || {
    echo 'GOTIFY_DATA_DIRECTORY_PREEXISTING_NONEMPTY=YES'
    exit 1
  }

  echo 'GOTIFY_DATA_DIRECTORY_PREEXISTING_EMPTY_SAFE=YES'
else
  install \
    -d \
    -o root \
    -g root \
    -m 0700 \
    "$GOTIFY_DATA_DIR"

  echo 'GOTIFY_DATA_DIRECTORY_CREATED=YES'
fi

export GOTIFY_IMAGE
export GOTIFY_DATA_DIR
export GOTIFY_ADMIN_PASSWORD_FILE_HOST="$GOTIFY_ADMIN_PASSWORD_FILE"

COMPOSE=(
  docker compose
  --env-file "$ENV_FILE"
  -f "$APP_ROOT/docker-compose.yml"
  -f "$STAGING_COMPOSE"
  -f "$GOTIFY_COMPOSE"
)

"${COMPOSE[@]}" config >/dev/null

echo 'GOTIFY_COMPOSE_CONFIG=PASS'

"${COMPOSE[@]}" pull gotify

docker image inspect \
  "$GOTIFY_IMAGE" \
  >/dev/null

echo 'GOTIFY_IMMUTABLE_IMAGE_PRESENT=PASS'

GOTIFY_START_ATTEMPTED='NO'

contain_failed_start() {
  local rc="$?"

  trap - EXIT

  if [[ "$rc" -ne 0 && "$GOTIFY_START_ATTEMPTED" == 'YES' ]]; then
    "${COMPOSE[@]}" stop \
      -t 10 \
      gotify \
      >/dev/null 2>&1 \
      || true

    echo 'GOTIFY_FAILURE_CONTAINMENT_STOP_ATTEMPTED=YES' >&2
  fi

  exit "$rc"
}

trap contain_failed_start EXIT

GOTIFY_START_ATTEMPTED='YES'

"${COMPOSE[@]}" up \
  -d \
  --no-deps \
  gotify

CID="$(
  "${COMPOSE[@]}" ps \
    -q gotify \
    </dev/null
)"

[[ -n "$CID" ]] || {
  echo 'GOTIFY_CONTAINER_ID_AVAILABLE=NO'
  exit 1
}

RUNNING="$(
  docker inspect \
    --format '{{.State.Running}}' \
    "$CID"
)"

[[ "$RUNNING" == 'true' ]]

PORT_BINDINGS="$(
  docker inspect \
    --format '{{json .HostConfig.PortBindings}}' \
    "$CID"
)"

PORT_BINDINGS="$PORT_BINDINGS" \
python3 <<'PY'
import json
import os

bindings = json.loads(
    os.environ["PORT_BINDINGS"]
)

expected = {
    "8080/tcp": [
        {
            "HostIp": "127.0.0.1",
            "HostPort": "8088",
        }
    ]
}

if bindings != expected:
    raise SystemExit(
        "GOTIFY_LOOPBACK_PORT_BINDING=FAIL"
    )

print(
    "GOTIFY_LOOPBACK_PORT_BINDING=PASS"
)
PY

HEALTH=''

for _ in $(seq 1 30); do
  HEALTH="$(
    curl \
      --silent \
      --noproxy '*' \
      --output /dev/null \
      --write-out '%{http_code}' \
      --connect-timeout 2 \
      --max-time 3 \
      http://127.0.0.1:8088/health \
      || true
  )"

  [[ "$HEALTH" == '200' ]] && break

  sleep 1
done

echo "GOTIFY_HEALTH_HTTP=$HEALTH"

[[ "$HEALTH" == '200' ]]

echo 'GOTIFY_HEALTH=PASS'

VERSION_JSON="$(
  curl \
    --silent \
    --show-error \
    --fail \
    --noproxy '*' \
    --connect-timeout 3 \
    --max-time 5 \
    http://127.0.0.1:8088/version
)"

GOTIFY_RUNTIME_VERSION="$(
  VERSION_JSON="$VERSION_JSON" \
  python3 <<'PY_VERSION'
import json
import os

payload = json.loads(
    os.environ["VERSION_JSON"]
)

version = payload.get("version")

if (
    not isinstance(version, str)
    or not version
):
    raise SystemExit(1)

print(version)
PY_VERSION
)"

unset VERSION_JSON

echo "GOTIFY_RUNTIME_VERSION=$GOTIFY_RUNTIME_VERSION"

[[ "$GOTIFY_RUNTIME_VERSION" == '3.1.0' ]] || {
  echo 'GOTIFY_RUNTIME_VERSION_CHECK=FAIL'
  exit 1
}

echo 'GOTIFY_RUNTIME_VERSION_CHECK=PASS'

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

trap - EXIT
GOTIFY_START_ATTEMPTED='NO'

echo 'ISSUE39_TIMERS_ACTIVE_AFTER=NO'
echo 'GOTIFY_FAILURE_CONTAINMENT_ARMED=NO'
echo 'GOTIFY_APP_TOKEN_CREATED=NO'
echo 'ALERT_ADAPTER_ACTIVATED=NO'
echo 'OPS_ALERT_CONFIG_CHANGED=NO'
echo 'FIREWALL_CHANGE=NO'
echo 'DNS_CHANGE=NO'
echo 'PRODUCTION_CHANGE=NO'

echo '============================================================'
echo 'ISSUE39_GOTIFY_SERVER_INSTALL=PASS'
echo 'NEXT_STEP=CREATE_GOTIFY_APPLICATION_TOKEN_THROUGH_PRIVATE_UI'
echo '============================================================'
