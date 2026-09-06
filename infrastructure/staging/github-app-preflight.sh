#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="${APP_ROOT:-/srv/inzozi-code/application}"
ENV_FILE="${ENV_FILE:-/srv/inzozi-code/.env.staging}"
API_URL="${API_URL:-http://127.0.0.1:8000}"
REPOSITORY_URL="${REPOSITORY_URL:-}"
STAGING_COMPOSE="${APP_ROOT}/infrastructure/staging/docker-compose.staging.yml"

if [[ ! -d "${APP_ROOT}/.git" ]]; then
  echo "Expected a Git checkout at ${APP_ROOT}." >&2
  exit 1
fi
if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Missing staging environment file: ${ENV_FILE}" >&2
  exit 1
fi
if [[ "$(stat -c '%a' "${ENV_FILE}")" != "600" ]]; then
  echo "Refusing preflight: ${ENV_FILE} must have mode 600." >&2
  exit 1
fi
if [[ ! -f "${STAGING_COMPOSE}" ]]; then
  echo "Missing staging Compose override: ${STAGING_COMPOSE}" >&2
  exit 1
fi

get_env() {
  local key="$1"
  local line
  line="$(grep -E "^${key}=" "${ENV_FILE}" | tail -n 1 || true)"
  printf '%s' "${line#*=}"
}

app_id="$(get_env GITHUB_APP_ID)"
owner="$(get_env GITHUB_APP_OWNER)"
installation_id="$(get_env GITHUB_APP_DEFAULT_INSTALLATION_ID)"
key_path="$(get_env GITHUB_APP_PRIVATE_KEY_HOST_PATH)"

if [[ ! "${app_id}" =~ ^[0-9]+$ ]]; then
  echo "GITHUB_APP_ID is missing or invalid in ${ENV_FILE}." >&2
  exit 1
fi
if [[ -z "${owner}" ]]; then
  echo "GITHUB_APP_OWNER is missing in ${ENV_FILE}." >&2
  exit 1
fi
if [[ ! "${installation_id}" =~ ^[0-9]+$ ]]; then
  echo "GITHUB_APP_DEFAULT_INSTALLATION_ID is missing or invalid in ${ENV_FILE}." >&2
  exit 1
fi
if [[ -z "${key_path}" ]] || ! sudo test -f "${key_path}"; then
  echo "GitHub App private key host file is missing." >&2
  exit 1
fi

key_mode="$(sudo stat -c '%a' "${key_path}")"
key_uid="$(sudo stat -c '%u' "${key_path}")"
if [[ "${key_mode}" != "400" || "${key_uid}" != "10001" ]]; then
  echo "GitHub App private key must be mode 400 and owned by UID 10001 for the non-root API container." >&2
  exit 1
fi

cd "${APP_ROOT}"
COMPOSE=(docker compose --env-file "${ENV_FILE}" -f docker-compose.yml -f "${STAGING_COMPOSE}")
"${COMPOSE[@]}" config --quiet

"${COMPOSE[@]}" exec -T api python - <<'PY'
from pathlib import Path
from app.core.config import get_settings

settings = get_settings()
path = Path(settings.github_app_private_key_path)
if not path.is_file():
    raise SystemExit("GitHub App key is not mounted inside the API container")
text = path.read_text(encoding="utf-8")
if "PRIVATE KEY" not in text:
    raise SystemExit("Mounted GitHub App key does not look like a PEM private key")
print("API container can read the mounted GitHub App key.")
PY

status_json="$(curl --fail --silent --show-error "${API_URL}/v1/github-app/status")"
printf '%s\n' "${status_json}"

STATUS_JSON="${status_json}" python3 - <<'PY'
import json
import os

payload = json.loads(os.environ["STATUS_JSON"])
if not payload.get("configured"):
    raise SystemExit("GitHub App status reports configured=false")
if not payload.get("owner_matches_installation"):
    raise SystemExit("GitHub App installation owner does not match the configured owner")
gates = payload.get("gates") or {}
missing = [name for name in ("private_clone", "remote_push", "draft_pull_request") if not gates.get(name)]
if missing:
    raise SystemExit("GitHub App permissions are incomplete for: " + ", ".join(missing))
print("GitHub App installation permissions are ready for clone, push, and draft PR gates.")
PY

if [[ -n "${REPOSITORY_URL}" ]]; then
  verify_payload="$(REPOSITORY_URL="${REPOSITORY_URL}" python3 - <<'PY'
import json
import os
print(json.dumps({"repository_url": os.environ["REPOSITORY_URL"]}))
PY
)"
  verify_json="$(curl --fail --silent --show-error \
    -H 'Content-Type: application/json' \
    -d "${verify_payload}" \
    "${API_URL}/v1/github-app/verify-repository")"
  printf '%s\n' "${verify_json}"
  VERIFY_JSON="${verify_json}" python3 - <<'PY'
import json
import os
payload = json.loads(os.environ["VERIFY_JSON"])
if not payload.get("verified") or payload.get("token_returned") is not False:
    raise SystemExit("Repository verification did not meet the expected secret boundary")
repository = payload.get("repository") or {}
if not repository.get("read_access_verified"):
    raise SystemExit("Repository-scoped read access was not verified")
print("Repository-scoped private clone preflight passed for " + repository.get("full_name", "repository"))
PY
else
  echo "Set REPOSITORY_URL to verify one selected private repository as an additional preflight."
fi
