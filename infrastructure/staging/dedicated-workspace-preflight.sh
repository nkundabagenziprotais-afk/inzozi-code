#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="${APP_ROOT:-/srv/inzozi-code/application}"
ENV_FILE="${ENV_FILE:-/srv/inzozi-code/.env.staging}"
STAGING_COMPOSE="${APP_ROOT}/infrastructure/staging/docker-compose.staging.yml"
RUNTIME_IMAGE="${WORKSPACE_RUNTIME_IMAGE:-inzozi-code-workspace-runtime:local}"
HELPER_NETWORK="inzozi-workspace-helper-restricted"
UPLINK_NETWORK="inzozi-workspace-egress-uplink"
PROXY_SERVICE="workspace-egress-proxy"
PROXY_URL="http://workspace-egress-proxy:3128"
LEGACY_HELPER_NETWORK="inzozi-workspace-egress"

cd "${APP_ROOT}"
COMPOSE=(docker compose --env-file "${ENV_FILE}" -f docker-compose.yml -f "${STAGING_COMPOSE}")

manager_id="$("${COMPOSE[@]}" ps -q workspace-manager)"
broker_id="$("${COMPOSE[@]}" ps -q workspace-broker)"
if [[ -z "${manager_id}" || -z "${broker_id}" ]]; then
  echo "Workspace manager and broker must both be running." >&2
  exit 1
fi

if ! docker image inspect "${RUNTIME_IMAGE}" >/dev/null 2>&1; then
  echo "Dedicated workspace runtime image is missing: ${RUNTIME_IMAGE}" >&2
  exit 1
fi

quota_image="$(docker inspect "${broker_id}" --format '{{range .Config.Env}}{{println .}}{{end}}' | sed -n 's/^WORKSPACE_QUOTA_HELPER_IMAGE=//p' | head -n1)"
quota_root="$(docker inspect "${broker_id}" --format '{{range .Config.Env}}{{println .}}{{end}}' | sed -n 's/^WORKSPACE_QUOTA_STORAGE_ROOT=//p' | head -n1)"
if [[ -z "${quota_image}" || -z "${quota_root}" ]]; then
  echo "Workspace XFS quota helper/storage configuration is missing from the broker." >&2
  exit 1
fi
if ! docker image inspect "${quota_image}" >/dev/null 2>&1; then
  echo "Workspace XFS quota helper image is missing: ${quota_image}" >&2
  exit 1
fi

manager_socket_mounts="$(docker inspect "${manager_id}" --format '{{range .Mounts}}{{if eq .Destination "/var/run/docker.sock"}}1{{end}}{{end}}')"
broker_socket_mounts="$(docker inspect "${broker_id}" --format '{{range .Mounts}}{{if eq .Destination "/var/run/docker.sock"}}1{{end}}{{end}}')"
manager_uid="$(docker exec "${manager_id}" id -u)"
if [[ -n "${manager_socket_mounts}" || "${broker_socket_mounts}" != "1" || "${manager_uid}" == "0" ]]; then
  echo "Workspace control-plane privilege boundary is invalid." >&2
  exit 1
fi

echo "Workspace manager is unprivileged and socket-free; only the narrow broker has Docker control."

control_internal="$(docker network inspect application_workspace-control --format '{{.Internal}}' 2>/dev/null || true)"
if [[ "${control_internal}" != "true" ]]; then
  control_network="$(docker inspect "${broker_id}" --format '{{range $name, $_ := .NetworkSettings.Networks}}{{println $name}}{{end}}' | grep 'workspace-control' | head -n1)"
  if [[ -z "${control_network}" ]] || [[ "$(docker network inspect "${control_network}" --format '{{.Internal}}')" != "true" ]]; then
    echo "Workspace broker control network must be internal." >&2
    exit 1
  fi
fi

echo "Workspace broker control network is internal."

status_json="$(docker exec "${manager_id}" python -c 'import urllib.request; print(urllib.request.urlopen("http://127.0.0.1:8200/v1/isolation/status", timeout=8).read().decode())')"
STATUS_JSON="${status_json}" python3 - <<'PY'
import json
import os

payload = json.loads(os.environ["STATUS_JSON"])
assert payload["status"] == "ok"
assert payload["mode"] == "brokered-dedicated-workspaces"
assert payload["workspace_socket_mounts"] == 0
assert payload["manager_docker_socket"] is False
assert payload["manager_privilege"] == "unprivileged"
assert payload["control_plane"] == "narrow-docker-broker"
assert payload["runtime_egress"] == "denied"
assert payload["git_helper_egress"] == "github-only-connect-proxy"
assert int(payload["disk_quota_bytes"]) > 0
assert payload["disk_quota_mode"] == "xfs-project-hard"
assert payload["quota_filesystem"] == "xfs"
assert payload["quota_project_enforcement"] is True
assert payload["all_active_workspaces_hard_quoted"] is True
assert payload["unique_active_project_ids"] is True
assert int(payload["max_active_workspaces"]) > 0
if payload["active_workspaces"]:
    assert payload["dedicated_networks"] is True
    assert payload["all_runtime_networks_internal"] is True
    assert all(item["disk_quota"] == "xfs-project-hard" for item in payload["workspaces"])
PY

echo "Brokered workspace isolation status reports XFS project hard quotas."

quota_probe="$(docker run --rm \
  --network none \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,nodev,size=67108864 \
  --user 0:0 \
  --cap-drop ALL \
  --cap-add SYS_ADMIN \
  --cap-add CHOWN \
  --security-opt no-new-privileges:true \
  --memory 256m \
  --pids-limit 64 \
  -v "${quota_root}:/quota-root:rw" \
  "${quota_image}" probe)"

QUOTA_PROBE="${quota_probe}" python3 - <<'PY'
import json
import os

payload = json.loads(os.environ["QUOTA_PROBE"].strip().splitlines()[-1])
assert payload["status"] == "ok"
assert payload["hard_limit_enforced"] is True
assert payload["failure_errno"] == "ENOSPC"
assert int(payload["probe_limit_bytes"]) == 1024 * 1024
PY

echo "XFS hard quota probe passed: a 2 MiB write was blocked by a 1 MiB project limit with ENOSPC."

set +e
docker exec "$("${COMPOSE[@]}" ps -q api)" python -c 'import socket; socket.getaddrinfo("workspace-broker", 8300)' >/dev/null 2>&1
api_broker_rc=$?
set -e
if [[ "${api_broker_rc}" -eq 0 ]]; then
  echo "Isolation failure: API service can resolve the privileged workspace broker." >&2
  exit 1
fi

echo "API service cannot directly resolve the privileged broker."

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

proxy_id="$("${COMPOSE[@]}" ps -q "${PROXY_SERVICE}")"

if [[ -z "${proxy_id}" ]]; then
  echo "Workspace GitHub egress proxy must be running." >&2
  exit 1
fi

proxy_uid="$(docker exec "${proxy_id}" id -u)"

proxy_socket_mounts="$(
  docker inspect "${proxy_id}" \
    --format '{{range .Mounts}}{{if eq .Destination "/var/run/docker.sock"}}1{{end}}{{end}}'
)"

if [[ "${proxy_uid}" == "0" || -n "${proxy_socket_mounts}" ]]; then
  echo "Workspace GitHub egress proxy must be non-root and socket-free." >&2
  exit 1
fi

helper_internal="$(
  docker network inspect \
    "${HELPER_NETWORK}" \
    --format '{{.Internal}}'
)"

uplink_internal="$(
  docker network inspect \
    "${UPLINK_NETWORK}" \
    --format '{{.Internal}}'
)"

if [[ "${helper_internal}" != "true" ]]; then
  echo "Restricted helper network must be internal." >&2
  exit 1
fi

if [[ "${uplink_internal}" != "false" ]]; then
  echo "Proxy uplink network must provide outbound connectivity." >&2
  exit 1
fi

echo "Workspace GitHub proxy topology is restricted as expected."

set +e

docker run --rm \
  --network "${HELPER_NETWORK}" \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,nodev,size=134217728 \
  --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --memory 128m \
  --pids-limit 32 \
  "${RUNTIME_IMAGE}" \
  python -c 'import urllib.request; urllib.request.urlopen("https://github.com", timeout=4)' \
  >/dev/null 2>&1

direct_rc=$?

set -e

if [[ "${direct_rc}" -eq 0 ]]; then
  echo "Isolation failure: direct helper egress still works." >&2
  exit 1
fi

echo "Direct helper internet egress is denied."

if ! docker run --rm \
  --network "${HELPER_NETWORK}" \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,nodev,size=134217728 \
  --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --memory 128m \
  --pids-limit 32 \
  -e "HTTPS_PROXY=${PROXY_URL}" \
  -e "https_proxy=${PROXY_URL}" \
  -e "HTTP_PROXY=${PROXY_URL}" \
  -e "http_proxy=${PROXY_URL}" \
  -e "NO_PROXY=" \
  -e "no_proxy=" \
  "${RUNTIME_IMAGE}" \
  python -c 'import urllib.request; r=urllib.request.urlopen("https://github.com", timeout=7); raise SystemExit(0 if r.status == 200 else 1)' \
  >/dev/null 2>&1
then
  echo "Restricted proxy cannot reach github.com." >&2
  exit 1
fi

echo "github.com:443 succeeds through the fixed proxy."

set +e

docker run --rm \
  --network "${HELPER_NETWORK}" \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,nodev,size=134217728 \
  --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --memory 128m \
  --pids-limit 32 \
  -e "HTTPS_PROXY=${PROXY_URL}" \
  -e "https_proxy=${PROXY_URL}" \
  -e "HTTP_PROXY=${PROXY_URL}" \
  -e "http_proxy=${PROXY_URL}" \
  -e "NO_PROXY=" \
  -e "no_proxy=" \
  "${RUNTIME_IMAGE}" \
  python -c 'import urllib.request; urllib.request.urlopen("https://example.com", timeout=5)' \
  >/dev/null 2>&1

non_github_rc=$?

set -e

if [[ "${non_github_rc}" -eq 0 ]]; then
  echo "Isolation failure: non-GitHub destination was allowed." >&2
  exit 1
fi

echo "Non-GitHub destinations are denied."

if docker network inspect \
  "${LEGACY_HELPER_NETWORK}" \
  >/dev/null 2>&1
then
  legacy_endpoints="$(
    docker network inspect \
      "${LEGACY_HELPER_NETWORK}" \
      --format '{{len .Containers}}'
  )"

  if [[ "${legacy_endpoints}" != "0" ]]; then
    echo "Legacy broad helper network still has endpoints." >&2
    exit 1
  fi
fi

echo "GitHub helper restricted-egress proxy preflight passed."

if docker ps --format '{{.Names}}' | grep -Eq '^application-workspace-1$'; then
  echo "Legacy shared workspace container is still running." >&2
  exit 1
fi

echo "Legacy shared workspace runtime is absent."
echo "Dedicated workspace preflight passed. Public staging remains disabled pending helper-egress allowlisting, durable Redis controls, and escape/exhaustion testing."
