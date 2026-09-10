#!/usr/bin/env bash
set -euo pipefail
umask 077

APP_ROOT="${APP_ROOT:-/srv/inzozi-code/application}"
EXPECTED_COMMIT_SHA="${EXPECTED_COMMIT_SHA:-}"
APPLY_DOCKER_LOG_ROTATION="${APPLY_DOCKER_LOG_ROTATION:-NO}"
RESTART_DOCKER="${RESTART_DOCKER:-NO}"
DAEMON_JSON="${DAEMON_JSON:-/etc/docker/daemon.json}"
MAX_SIZE="${DOCKER_LOG_MAX_SIZE:-10m}"
MAX_FILE="${DOCKER_LOG_MAX_FILE:-5}"

fail() {
  printf 'DOCKER_LOG_ROTATION_ERROR=%s\n' "$1" >&2
  exit 1
}

[[ "${EUID}" -eq 0 ]] || fail "must-run-as-root"
[[ "$EXPECTED_COMMIT_SHA" =~ ^[0-9a-f]{40}$ ]] || fail "expected-commit-sha-required"
[[ "$MAX_SIZE" =~ ^[1-9][0-9]*[kKmMgG]$ ]] || fail "invalid-max-size"
[[ "$MAX_FILE" =~ ^[1-9][0-9]*$ ]] || fail "invalid-max-file"
[[ -d "${APP_ROOT}/.git" ]] || fail "missing-git-checkout"
command -v python3 >/dev/null 2>&1 || fail "python3-unavailable"
command -v dockerd >/dev/null 2>&1 || fail "dockerd-unavailable"

cd "$APP_ROOT"

[[ "$(git rev-parse HEAD)" == "$EXPECTED_COMMIT_SHA" ]] || fail "checkout-sha-mismatch"
[[ -z "$(git status --porcelain)" ]] || fail "checkout-dirty"

TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
chmod 0600 "$TMP"

python3 - \
  "$DAEMON_JSON" \
  "$TMP" \
  "$MAX_SIZE" \
  "$MAX_FILE" <<'PY'
import json
import os
import sys

source, target, max_size, max_file = sys.argv[1:]

if os.path.exists(source):
    with open(source, "r", encoding="utf-8") as handle:
        data = json.load(handle)
else:
    data = {}

if not isinstance(data, dict):
    raise SystemExit("Docker daemon config must be a JSON object")

existing_driver = data.get("log-driver")
if existing_driver not in (None, "json-file"):
    raise SystemExit("Existing Docker log driver is not json-file")

data["log-driver"] = "json-file"
opts = data.get("log-opts") or {}
if not isinstance(opts, dict):
    raise SystemExit("Docker log-opts must be a JSON object")

opts["max-size"] = str(max_size)
opts["max-file"] = str(max_file)
data["log-opts"] = opts

with open(target, "w", encoding="utf-8") as handle:
    json.dump(data, handle, indent=2, sort_keys=True)
    handle.write("\n")
PY

dockerd --validate --config-file "$TMP" >/dev/null

printf '%s\n' 'DOCKER_LOG_ROTATION_CANDIDATE=PASS'
printf 'DOCKER_LOG_MAX_SIZE=%s\n' "$MAX_SIZE"
printf 'DOCKER_LOG_MAX_FILE=%s\n' "$MAX_FILE"
printf '%s\n' 'EXISTING_DOCKER_CONFIG_FIELDS_PRESERVED=YES'
printf '%s\n' 'DOCKER_RESTART_THIS_STEP=NO'

if [[ "$APPLY_DOCKER_LOG_ROTATION" != 'YES' ]]; then
  printf '%s\n' 'DOCKER_LOG_ROTATION_APPLIED=NO'
  printf '%s\n' 'NEXT_STEP=EXPLICITLY_SET_APPLY_DOCKER_LOG_ROTATION_YES_AFTER_REVIEW'
  exit 0
fi

install -d -m 0755 "$(dirname "$DAEMON_JSON")"
install -m 0644 "$TMP" "$DAEMON_JSON"

dockerd --validate --config-file "$DAEMON_JSON" >/dev/null

printf '%s\n' 'DOCKER_LOG_ROTATION_APPLIED=PASS'
printf '%s\n' 'DOCKER_DAEMON_CONFIG_VALID=PASS'

if [[ "$RESTART_DOCKER" != 'YES' ]]; then
  printf '%s\n' 'DOCKER_RESTART_THIS_STEP=NO'
  printf '%s\n' 'DOCKER_LOG_ROTATION_ACTIVE_FOR_NEW_DAEMON_SESSION=NO'
  printf '%s\n' 'NEXT_STEP=CONTROLLED_DOCKER_RESTART_WHEN_ZERO_WORKSPACES_CONFIRMED'
  exit 0
fi

WORKSPACE_COUNT="$(
  docker ps -a \
    --filter 'label=com.inzozi.code.workspace=true' \
    --format '{{.ID}}' |
  wc -l |
  tr -d ' '
)"

[[ "$WORKSPACE_COUNT" == '0' ]] || fail "refuse-docker-restart-with-workspace-runtime-residue"

systemctl restart docker
systemctl is-active --quiet docker || fail "docker-not-active-after-restart"

printf '%s\n' 'ACTIVE_WORKSPACE_COUNT_BEFORE_DOCKER_RESTART=0'
printf '%s\n' 'DOCKER_RESTART_THIS_STEP=YES'
printf '%s\n' 'DOCKER_LOG_ROTATION_ACTIVE_FOR_NEW_DAEMON_SESSION=PASS'
printf '%s\n' 'ISSUE39_DOCKER_LOG_ROTATION=PASS'
