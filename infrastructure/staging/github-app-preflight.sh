#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="${APP_ROOT:-/srv/inzozi-code/application}"
ENV_FILE="${ENV_FILE:-/srv/inzozi-code/.env.staging}"
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

"${COMPOSE[@]}" exec -T -e REPOSITORY_URL="${REPOSITORY_URL}" api python - <<'PY'
import asyncio
import json
import os
from pathlib import Path

from app.core.config import get_settings
from app.integrations.github_app import (
    default_installation_for,
    get_installation_summary,
    github_app_configured,
    verify_repository_access,
)


async def main() -> None:
    settings = get_settings()
    path = Path(settings.github_app_private_key_path)
    if not path.is_file():
        raise SystemExit("GitHub App key is not mounted inside the API container")
    text = path.read_text(encoding="utf-8")
    if "PRIVATE KEY" not in text:
        raise SystemExit("Mounted GitHub App key does not look like a PEM private key")
    print("API container can read the mounted GitHub App key.")

    if not github_app_configured():
        raise SystemExit("GitHub App staging credentials are not fully configured")
    installation_id = int(settings.github_app_default_installation_id)
    summary = await get_installation_summary(installation_id)
    owner_matches = bool(summary["account"]) and summary["account"].casefold() == settings.github_app_owner.casefold()
    readiness = summary["readiness"]
    gates = {
        "private_clone": owner_matches and not summary["suspended"] and readiness["private_clone"],
        "remote_push": owner_matches and not summary["suspended"] and readiness["remote_push"],
        "draft_pull_request": owner_matches and not summary["suspended"] and readiness["draft_pull_request"],
    }
    safe_status = {
        "configured": True,
        "owner": settings.github_app_owner,
        "owner_matches_installation": owner_matches,
        "key_loaded": True,
        "installation": {
            "account": summary["account"],
            "repository_selection": summary["repository_selection"],
            "permissions": summary["permissions"],
            "suspended": summary["suspended"],
        },
        "gates": gates,
    }
    print(json.dumps(safe_status, separators=(",", ":")))
    missing = [name for name, ready in gates.items() if not ready]
    if missing:
        raise SystemExit("GitHub App permissions are incomplete for: " + ", ".join(missing))
    print("GitHub App installation permissions are ready for clone, push, and draft PR gates.")

    repository_url = os.environ.get("REPOSITORY_URL", "").strip()
    if not repository_url:
        print("Set REPOSITORY_URL to verify one selected private repository as an additional preflight.")
        return
    repository_installation = default_installation_for(repository_url)
    if repository_installation is None:
        raise SystemExit("Repository owner does not match the configured GitHub App installation owner")
    repository = await verify_repository_access(repository_url, repository_installation)
    safe_repository = {
        "verified": True,
        "repository": repository,
        "token_returned": False,
        "permission_used": "contents:read",
    }
    print(json.dumps(safe_repository, separators=(",", ":")))
    if not repository.get("read_access_verified"):
        raise SystemExit("Repository-scoped read access was not verified")
    print("Repository-scoped private clone preflight passed for " + repository.get("full_name", "repository"))


asyncio.run(main())
PY
