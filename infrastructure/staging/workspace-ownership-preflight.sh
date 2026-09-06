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

manager_container="$(${COMPOSE[@]} ps -q workspace-manager)"
if [[ -n "${manager_container}" ]]; then
  status_json="$(docker exec "${manager_container}" python -c 'import urllib.request; print(urllib.request.urlopen("http://127.0.0.1:8200/v1/isolation/status", timeout=5).read().decode())')"
  STATUS_JSON="${status_json}" python3 - <<'PY'
import json
import os
payload = json.loads(os.environ["STATUS_JSON"])
assert payload["mode"] == "dedicated-per-workspace-containers"
assert payload["workspace_socket_mounts"] == 0
assert payload["runtime_egress"] == "denied"
if payload["active_workspaces"]:
    assert payload["dedicated_networks"] is True
    assert payload["all_runtime_networks_internal"] is True
PY
  echo "Workspace ownership is paired with dedicated per-workspace execution and egress-denied runtimes."
  echo "Workspace ownership preflight passed."
  exit 0
fi

echo "Neither the dedicated workspace manager nor a supported legacy workspace runtime is available." >&2
exit 1
