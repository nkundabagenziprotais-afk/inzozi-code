#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="${APP_ROOT:-/srv/inzozi-code/application}"
ENV_FILE="${ENV_FILE:-/srv/inzozi-code/.env.staging}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8080/health}"
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
  echo "Refusing deployment: ${ENV_FILE} must have mode 600." >&2
  exit 1
fi

if [[ ! -f "${STAGING_COMPOSE}" ]]; then
  echo "Missing staging Compose override: ${STAGING_COMPOSE}" >&2
  exit 1
fi

cd "${APP_ROOT}"

if [[ -n "$(git status --porcelain)" ]]; then
  echo "Refusing deployment from a dirty Git worktree." >&2
  git status --short
  exit 1
fi

COMMIT_SHA="$(git rev-parse HEAD)"
BRANCH_NAME="$(git branch --show-current || true)"
COMPOSE=(docker compose --env-file "${ENV_FILE}" -f docker-compose.yml -f "${STAGING_COMPOSE}")

echo "Deploying Inzozi Code staging"
echo "Branch: ${BRANCH_NAME:-detached}"
echo "Commit: ${COMMIT_SHA}"

# Validate configuration before build/start. Secrets are supplied by the untracked server env file.
"${COMPOSE[@]}" config --quiet

# The runtime image is build-only. The narrow privileged broker launches isolated
# runtime/helper containers; the unprivileged manager never receives Docker access.
"${COMPOSE[@]}" --profile workspace-runtime-image build --pull
"${COMPOSE[@]}" up -d --remove-orphans

healthy=0
for _ in $(seq 1 40); do
  if curl --fail --silent --show-error "${HEALTH_URL}" >/dev/null; then
    healthy=1
    break
  fi
  sleep 2
done

if [[ "${healthy}" -ne 1 ]]; then
  echo "Staging health check failed: ${HEALTH_URL}" >&2
  "${COMPOSE[@]}" ps >&2
  "${COMPOSE[@]}" logs --tail=160 api workspace-manager workspace-broker nginx >&2 || true
  exit 1
fi

echo "Health check passed: ${HEALTH_URL}"
"${COMPOSE[@]}" ps
printf 'deployed_commit=%s\n' "${COMMIT_SHA}"
