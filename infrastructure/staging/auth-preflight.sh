#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="${APP_ROOT:-/srv/inzozi-code/application}"
ENV_FILE="${ENV_FILE:-/srv/inzozi-code/.env.staging}"
API_URL="${API_URL:-http://127.0.0.1:8000}"
STAGING_COMPOSE="${APP_ROOT}/infrastructure/staging/docker-compose.staging.yml"
REQUIRE_SECURE_COOKIE="${REQUIRE_SECURE_COOKIE:-false}"

if [[ "${REQUIRE_SECURE_COOKIE}" != "true" && "${REQUIRE_SECURE_COOKIE}" != "false" ]]; then
  echo "REQUIRE_SECURE_COOKIE must be true or false." >&2
  exit 1
fi

if [[ ! -d "${APP_ROOT}/.git" ]]; then
  echo "Expected a Git checkout at ${APP_ROOT}." >&2
  exit 1
fi
if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Missing staging environment file: ${ENV_FILE}" >&2
  exit 1
fi
if [[ "$(stat -c '%a' "${ENV_FILE}")" != "600" ]]; then
  echo "Refusing auth preflight: ${ENV_FILE} must have mode 600." >&2
  exit 1
fi
if [[ ! -f "${STAGING_COMPOSE}" ]]; then
  echo "Missing staging Compose override: ${STAGING_COMPOSE}" >&2
  exit 1
fi
if grep -Eq '^AUTH_PASSWORD=' "${ENV_FILE}"; then
  echo "Refusing auth preflight: plaintext AUTH_PASSWORD must not be present." >&2
  exit 1
fi

get_env() {
  local key="$1"
  local line
  line="$(grep -E "^${key}=" "${ENV_FILE}" | tail -n 1 || true)"
  printf '%s' "${line#*=}"
}

auth_enabled="$(get_env AUTH_ENABLED)"
auth_email="$(get_env AUTH_BOOTSTRAP_EMAIL)"
auth_hash_raw="$(get_env AUTH_PASSWORD_HASH)"
auth_secret="$(get_env AUTH_SESSION_SECRET)"
auth_role="$(get_env AUTH_BOOTSTRAP_ROLE)"
auth_cookie_secure="$(get_env AUTH_COOKIE_SECURE)"

if [[ "${auth_enabled}" != "true" ]]; then
  echo "AUTH_ENABLED must be true for staging authentication preflight." >&2
  exit 1
fi
if [[ -z "${auth_email}" || "${auth_email}" != *"@"* ]]; then
  echo "AUTH_BOOTSTRAP_EMAIL is missing or invalid." >&2
  exit 1
fi
if [[ "${auth_hash_raw}" != \'pbkdf2_sha256\$*\' ]]; then
  echo "AUTH_PASSWORD_HASH must be a single-quoted pbkdf2_sha256 hash so Compose cannot interpolate its dollar fields." >&2
  exit 1
fi
auth_hash="${auth_hash_raw:1:${#auth_hash_raw}-2}"
if [[ "${auth_hash}" != pbkdf2_sha256\$* ]]; then
  echo "AUTH_PASSWORD_HASH must use the pbkdf2_sha256 format." >&2
  exit 1
fi
if (( ${#auth_secret} < 32 )); then
  echo "AUTH_SESSION_SECRET is missing or too short." >&2
  exit 1
fi
case "${auth_role}" in
  platform_owner|org_admin|developer|reviewer|deployment_approver|viewer) ;;
  *) echo "AUTH_BOOTSTRAP_ROLE is invalid." >&2; exit 1 ;;
esac
if [[ "${auth_cookie_secure}" != "true" && "${auth_cookie_secure}" != "false" ]]; then
  echo "AUTH_COOKIE_SECURE must be true or false." >&2
  exit 1
fi
if [[ "${REQUIRE_SECURE_COOKIE}" == "true" && "${auth_cookie_secure}" != "true" ]]; then
  echo "AUTH_COOKIE_SECURE must be true when REQUIRE_SECURE_COOKIE=true." >&2
  exit 1
fi

host_hash_fingerprint="$(printf '%s' "${auth_hash}" | sha256sum | awk '{print $1}')"
unset auth_hash

cd "${APP_ROOT}"
COMPOSE=(docker compose --env-file "${ENV_FILE}" -f docker-compose.yml -f "${STAGING_COMPOSE}")
"${COMPOSE[@]}" config --quiet

container_hash_fingerprint="$("${COMPOSE[@]}" exec -T -e EXPECTED_COOKIE_SECURE="${auth_cookie_secure}" api python - <<'PY'
import hashlib
import os

from app.core.config import get_settings
from app.security.auth import ROLE_PERMISSIONS

settings = get_settings()
if not settings.auth_enabled:
    raise SystemExit("API container reports auth_enabled=false")
if not settings.auth_bootstrap_email:
    raise SystemExit("API container is missing the bootstrap email")
if not settings.auth_password_hash.startswith("pbkdf2_sha256$"):
    raise SystemExit("API container password hash format is invalid")
if len(settings.auth_session_secret) < 32:
    raise SystemExit("API container session secret is too short")
if settings.auth_bootstrap_role not in ROLE_PERMISSIONS:
    raise SystemExit("API container bootstrap role is invalid")
expected_cookie_secure = os.environ.get("EXPECTED_COOKIE_SECURE") == "true"
if settings.auth_cookie_secure is not expected_cookie_secure:
    raise SystemExit("API container AUTH_COOKIE_SECURE does not match the host environment")
print(hashlib.sha256(settings.auth_password_hash.encode("utf-8")).hexdigest())
PY
)"

if [[ "${container_hash_fingerprint}" != "${host_hash_fingerprint}" ]]; then
  echo "Refusing auth preflight: AUTH_PASSWORD_HASH changed while passing through Docker Compose." >&2
  exit 1
fi
printf 'API container authentication configuration is loaded and structurally valid.\n'
printf 'Authentication hash survived Docker Compose interpolation unchanged.\n'

health_json="$(curl --fail --silent --show-error "${API_URL}/health")"
HEALTH_JSON="${health_json}" python3 - <<'PY'
import json
import os
payload = json.loads(os.environ["HEALTH_JSON"])
if payload.get("status") != "ok" or payload.get("auth_enabled") is not True:
    raise SystemExit("Health endpoint does not report active authentication")
print("Health endpoint reports staging authentication enabled.")
PY

me_status="$(curl --silent --output /tmp/inzozi-auth-me.json --write-out '%{http_code}' "${API_URL}/v1/auth/me")"
rm -f /tmp/inzozi-auth-me.json
if [[ "${me_status}" != "401" ]]; then
  echo "Unauthenticated /v1/auth/me must return 401; received ${me_status}." >&2
  exit 1
fi

providers_status="$(curl --silent --output /tmp/inzozi-auth-providers.json --write-out '%{http_code}' "${API_URL}/v1/agent/providers")"
rm -f /tmp/inzozi-auth-providers.json
if [[ "${providers_status}" != "401" ]]; then
  echo "Unauthenticated protected API request must return 401; received ${providers_status}." >&2
  exit 1
fi

printf 'Unauthenticated API access is blocked as expected.\n'
printf 'Staging auth preflight passed. role=%s cookie_secure=%s\n' "${auth_role}" "${auth_cookie_secure}"
