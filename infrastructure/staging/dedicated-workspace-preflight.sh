#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="${APP_ROOT:-/srv/inzozi-code/application}"
ENV_FILE="${ENV_FILE:-/srv/inzozi-code/.env.staging}"
STAGING_COMPOSE="${APP_ROOT}/infrastructure/staging/docker-compose.staging.yml"
RUNTIME_IMAGE="${WORKSPACE_RUNTIME_IMAGE:-inzozi-code-workspace-runtime:local}"
HELPER_NETWORK="${WORKSPACE_EGRESS_NETWORK:-inzozi-workspace-egress}"

cd "${APP_ROOT}"
COMPOSE=(docker compose --env-file "${ENV_FILE}" -f docker-compose.yml -f "${STAGING_COMPOSE}")

manager_id="$("${COMPOSE[@]}" ps -q workspace-manager)"
if [[ -z "${manager_id}" ]]; then
  echo "Workspace manager container is not running." >&2
  exit 1
fi

if ! docker image inspect "${RUNTIME_IMAGE}" >/dev/null 2>&1; then
  echo "Dedicated workspace runtime image is missing: ${RUNTIME_IMAGE}" >&2
  exit 1
fi

manager_socket_mounts="$(docker inspect "${manager_id}" --format '{{range .Mounts}}{{if eq .Destination "/var/run/docker.sock"}}1{{end}}{{end}}')"
if [[ "${manager_socket_mounts}" != "1" ]]; then
  echo "Trusted workspace manager does not have the expected Docker control socket." >&2
  exit 1
fi

echo "Trusted workspace manager has Docker control; workspace runtimes do not inherit the socket."

status_json="$(docker exec "${manager_id}" python -c 'import urllib.request; print(urllib.request.urlopen("http://127.0.0.1:8200/v1/isolation/status", timeout=5).read().decode())')"
STATUS_JSON="${status_json}" python3 - <<'PY'
import json
import os

payload = json.loads(os.environ["STATUS_JSON"])
assert payload["status"] == "ok"
assert payload["mode"] == "dedicated-per-workspace-containers"
assert payload["workspace_socket_mounts"] == 0
assert payload["runtime_egress"] == "denied"
assert int(payload["ttl_seconds"]) > 0
assert int(payload["disk_quota_bytes"]) > 0
assert int(payload["max_active_workspaces"]) > 0
if payload["active_workspaces"]:
    assert payload["dedicated_networks"] is True
    assert payload["all_runtime_networks_internal"] is True
PY

echo "Workspace manager reports dedicated containers, zero workspace Docker sockets, TTL and disk limits."

probe_network="inzozi-preflight-isolated-$$"
cleanup() {
  docker network rm "${probe_network}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker network create --internal "${probe_network}" >/dev/null
set +e
docker run --rm \
  --network "${probe_network}" \
  --read-only \
  --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --memory 128m \
  --pids-limit 32 \
  "${RUNTIME_IMAGE}" \
  python -c 'import urllib.request; urllib.request.urlopen("https://github.com", timeout=3)' \
  >/dev/null 2>&1
probe_rc=$?
set -e

if [[ "${probe_rc}" -eq 0 ]]; then
  echo "Isolation failure: an internal workspace network reached the public internet." >&2
  exit 1
fi

echo "Runtime egress-deny probe passed: isolated workspace networks cannot reach github.com."

helper_internal="$(docker network inspect "${HELPER_NETWORK}" --format '{{.Internal}}')"
if [[ "${helper_internal}" != "false" ]]; then
  echo "Workspace helper egress network must not be internal." >&2
  exit 1
fi

if ! docker run --rm \
  --network "${HELPER_NETWORK}" \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,nodev,size=134217728 \
  --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --memory 128m \
  --pids-limit 32 \
  "${RUNTIME_IMAGE}" \
  python -c 'import urllib.request; response=urllib.request.urlopen("https://github.com", timeout=5); raise SystemExit(0 if response.status == 200 else 1)' \
  >/dev/null 2>&1; then
  echo "Workspace helper egress network cannot reach GitHub." >&2
  exit 1
fi

echo "GitHub helper egress probe passed."

if docker ps --format '{{.Names}}' | grep -Eq '^application-workspace-1$'; then
  echo "Legacy shared workspace container is still running." >&2
  exit 1
fi

echo "Legacy shared workspace runtime is absent."
echo "Dedicated workspace preflight passed. Public staging remains disabled pending live workspace E2E and residual host-disk/manager-broker review."
