#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="${APP_ROOT:-/srv/inzozi-code/application}"
ENV_FILE="${ENV_FILE:-/srv/inzozi-code/.env.staging}"
STAGING_COMPOSE="${APP_ROOT}/infrastructure/staging/docker-compose.staging.yml"

if [[ ! -d "${APP_ROOT}/.git" || ! -f "${ENV_FILE}" || ! -f "${STAGING_COMPOSE}" ]]; then
  echo "Workspace ownership preflight requires the staging checkout, env file and Compose override." >&2
  exit 1
fi
if [[ "$(stat -c '%a' "${ENV_FILE}")" != "600" ]]; then
  echo "Refusing ownership preflight: ${ENV_FILE} must have mode 600." >&2
  exit 1
fi

cd "${APP_ROOT}"
COMPOSE=(docker compose --env-file "${ENV_FILE}" -f docker-compose.yml -f "${STAGING_COMPOSE}")
"${COMPOSE[@]}" config --quiet

"${COMPOSE[@]}" exec -T api python - <<'PY'
from app.core.config import get_settings
from app.security.workspace_ownership import ensure_workspace_ownership_schema
import psycopg

settings = get_settings()
if not settings.auth_enabled:
    raise SystemExit("Authentication must remain enabled before workspace ownership enforcement")
if not settings.workspace_ownership_enforced:
    raise SystemExit("API reports workspace_ownership_enforced=false")
ensure_workspace_ownership_schema()
with psycopg.connect(settings.database_url) as connection, connection.cursor() as cursor:
    cursor.execute("SELECT to_regclass('public.workspace_ownership'), to_regclass('public.workspace_access_audit')")
    ownership_table, audit_table = cursor.fetchone()
if not ownership_table or not audit_table:
    raise SystemExit("Durable workspace ownership/audit tables are missing")
print("Durable workspace ownership and audit registry is ready.")
PY

workspace_container="$(${COMPOSE[@]} ps -q workspace)"
if [[ -z "${workspace_container}" ]]; then
  echo "Workspace container is not running." >&2
  exit 1
fi

read_only="$(docker inspect -f '{{.HostConfig.ReadonlyRootfs}}' "${workspace_container}")"
cap_drop="$(docker inspect -f '{{json .HostConfig.CapDrop}}' "${workspace_container}")"
security_opt="$(docker inspect -f '{{json .HostConfig.SecurityOpt}}' "${workspace_container}")"
pids_limit="$(docker inspect -f '{{.HostConfig.PidsLimit}}' "${workspace_container}")"
memory="$(docker inspect -f '{{.HostConfig.Memory}}' "${workspace_container}")"
nano_cpus="$(docker inspect -f '{{.HostConfig.NanoCpus}}' "${workspace_container}")"

if [[ "${read_only}" != "true" ]]; then
  echo "Workspace runtime root filesystem must be read-only." >&2
  exit 1
fi
if [[ "${cap_drop}" != *"ALL"* ]]; then
  echo "Workspace runtime must drop all Linux capabilities." >&2
  exit 1
fi
if [[ "${security_opt}" != *"no-new-privileges"* ]]; then
  echo "Workspace runtime must enable no-new-privileges." >&2
  exit 1
fi
if [[ "${pids_limit}" == "0" || "${pids_limit}" == "-1" ]]; then
  echo "Workspace runtime must have a PID limit." >&2
  exit 1
fi
if [[ "${memory}" == "0" || "${nano_cpus}" == "0" ]]; then
  echo "Workspace runtime must have explicit memory and CPU limits." >&2
  exit 1
fi

printf 'Workspace runtime hardening is active: read-only rootfs, capabilities dropped, no-new-privileges, CPU/memory/PID limits.\n'
printf 'Workspace ownership preflight passed. Public staging remains blocked pending dedicated per-workspace execution and egress controls.\n'
