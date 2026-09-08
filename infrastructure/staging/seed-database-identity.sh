#!/usr/bin/env bash
# Seed the first durable platform owner from existing bootstrap credentials.
# Does not switch AUTH_IDENTITY_MODE, print secrets, or change DNS/firewall/Nginx.
set -euo pipefail

APP_ROOT="${APP_ROOT:-/srv/inzozi-code/application}"
ENV_FILE="${ENV_FILE:-/srv/inzozi-code/.env.staging}"
STAGING_COMPOSE="${APP_ROOT}/infrastructure/staging/docker-compose.staging.yml"
EXPECTED_COMMIT_SHA="${EXPECTED_COMMIT_SHA:-}"

if [[ ! -d "${APP_ROOT}/.git" ]]; then
  echo "Expected a Git checkout at ${APP_ROOT}." >&2
  exit 1
fi
if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Missing staging environment file: ${ENV_FILE}" >&2
  exit 1
fi
if [[ "$(stat -c '%a' "${ENV_FILE}")" != "600" ]]; then
  echo "Refusing identity seed: ${ENV_FILE} must have mode 600." >&2
  exit 1
fi
if [[ ! -f "${STAGING_COMPOSE}" ]]; then
  echo "Missing staging Compose override: ${STAGING_COMPOSE}" >&2
  exit 1
fi
if [[ ! "${EXPECTED_COMMIT_SHA}" =~ ^[0-9a-f]{40}$ ]]; then
  echo "Refusing identity seed: EXPECTED_COMMIT_SHA must be the exact reviewed 40-character SHA." >&2
  exit 1
fi

cd "${APP_ROOT}"

if [[ -n "$(git status --porcelain)" ]]; then
  echo "Refusing identity seed from a dirty Git worktree." >&2
  git status --short
  exit 1
fi

COMMIT_SHA="$(git rev-parse HEAD)"
if [[ "${COMMIT_SHA}" != "${EXPECTED_COMMIT_SHA}" ]]; then
  echo "Refusing identity seed: checkout ${COMMIT_SHA} does not match reviewed ${EXPECTED_COMMIT_SHA}." >&2
  exit 1
fi

get_env() {
  local key="$1"
  local line
  line="$(grep -E "^${key}=" "${ENV_FILE}" | tail -n 1 || true)"
  printf '%s' "${line#*=}"
}

auth_enabled="$(get_env AUTH_ENABLED)"
identity_mode="$(get_env AUTH_IDENTITY_MODE)"
auth_email="$(get_env AUTH_BOOTSTRAP_EMAIL)"
auth_hash_raw="$(get_env AUTH_PASSWORD_HASH)"
auth_role="$(get_env AUTH_BOOTSTRAP_ROLE)"
auth_org="$(get_env AUTH_ORGANIZATION_ID)"

if [[ "${auth_enabled}" != "true" ]]; then
  echo "AUTH_ENABLED must be true before seeding durable identity." >&2
  exit 1
fi
# Seed is performed while still in bootstrap mode; operators switch modes separately.
if [[ -z "${identity_mode}" ]]; then
  identity_mode="bootstrap"
fi
if [[ "${identity_mode}" != "bootstrap" ]]; then
  echo "Identity seed requires AUTH_IDENTITY_MODE=bootstrap (no mode switch performed by this helper)." >&2
  exit 1
fi
if [[ -z "${auth_email}" || "${auth_email}" != *"@"* ]]; then
  echo "AUTH_BOOTSTRAP_EMAIL is missing or invalid." >&2
  exit 1
fi
if [[ "${auth_hash_raw}" != \'pbkdf2_sha256\$*\' ]]; then
  echo "AUTH_PASSWORD_HASH must be a single-quoted pbkdf2_sha256 hash." >&2
  exit 1
fi
auth_hash="${auth_hash_raw:1:${#auth_hash_raw}-2}"
if [[ "${auth_hash}" != pbkdf2_sha256\$* ]]; then
  echo "AUTH_PASSWORD_HASH must use the pbkdf2_sha256 format." >&2
  exit 1
fi
if [[ -z "${auth_org}" ]]; then
  echo "AUTH_ORGANIZATION_ID is required." >&2
  exit 1
fi
case "${auth_role}" in
  platform_owner) ;;
  *) echo "Seed helper may only create the platform_owner role." >&2; exit 1 ;;
esac

# Clear local copies of secrets after exporting into the container env only.
unset auth_hash_raw

COMPOSE=(docker compose --env-file "${ENV_FILE}" -f docker-compose.yml -f "${STAGING_COMPOSE}")
"${COMPOSE[@]}" config --quiet

# Pass bootstrap material via env; never print hash/email/credentials.
SEED_STATUS="$("${COMPOSE[@]}" exec -T \
  -e SEED_EMAIL="${auth_email}" \
  -e SEED_PASSWORD_HASH="${auth_hash}" \
  -e SEED_ORGANIZATION_ID="${auth_org}" \
  -e SEED_ROLE="${auth_role}" \
  api python - <<'PY'
import os
import sys

from app.security.identity_store import (
    IdentityConflictError,
    ensure_identity_schema,
    seed_platform_owner,
)

email = os.environ.get("SEED_EMAIL", "").strip()
password_hash = os.environ.get("SEED_PASSWORD_HASH", "").strip()
organization_id = os.environ.get("SEED_ORGANIZATION_ID", "").strip()
role = os.environ.get("SEED_ROLE", "platform_owner").strip()

if not email or not password_hash or not organization_id:
    raise SystemExit("SEED_MISSING_MATERIAL")

ensure_identity_schema()
try:
    user = seed_platform_owner(
        email=email,
        password_hash=password_hash,
        organization_id=organization_id,
        role=role,
    )
except IdentityConflictError:
    raise SystemExit("SEED_CONFLICT")
except Exception:
    raise SystemExit("SEED_FAILED")

# Do not print email, hash, or credentials — only opaque status + role/status.
print(f"SEED_OK status={user.status} role={user.role}")
PY
)"

unset auth_hash auth_email

case "${SEED_STATUS}" in
  SEED_OK*)
    printf 'Durable platform owner seed completed idempotently.\n'
    printf 'AUTH_IDENTITY_MODE was not changed (still bootstrap).\n'
    printf 'No DNS, firewall, or Nginx changes were made.\n'
    ;;
  *SEED_CONFLICT*)
    echo "Refusing identity seed: conflicting durable identity already exists." >&2
    exit 1
    ;;
  *)
    echo "Identity seed failed." >&2
    exit 1
    ;;
esac
