#!/usr/bin/env bash
set -euo pipefail
umask 077

APP_ROOT="${APP_ROOT:-/srv/inzozi-code/application}"
ENV_FILE="${ENV_FILE:-/srv/inzozi-code/.env.staging}"
DOMAIN="${DOMAIN:-code-staging.inzozidigital.com}"
ROLLBACK_COMMIT_SHA="${ROLLBACK_COMMIT_SHA:-}"
CONFIRM_PUBLIC_DNS_DISABLED="${CONFIRM_PUBLIC_DNS_DISABLED:-NO}"
CONFIRM_FIREWALL_PRIVATE="${CONFIRM_FIREWALL_PRIVATE:-NO}"
CONFIRM_DATABASE_RESTORE_NOT_REQUESTED="${CONFIRM_DATABASE_RESTORE_NOT_REQUESTED:-NO}"

fail() {
  printf 'PRIVATE_ROLLBACK_ERROR=%s\n' "$1" >&2
  exit 1
}

[[ "${EUID}" -eq 0 ]] || fail "must-run-as-root"
[[ "$ROLLBACK_COMMIT_SHA" =~ ^[0-9a-f]{40}$ ]] || fail "rollback-commit-sha-required"
[[ "$CONFIRM_PUBLIC_DNS_DISABLED" == 'YES' ]] || fail "public-dns-disable-confirmation-required"
[[ "$CONFIRM_FIREWALL_PRIVATE" == 'YES' ]] || fail "private-firewall-confirmation-required"
[[ "$CONFIRM_DATABASE_RESTORE_NOT_REQUESTED" == 'YES' ]] || fail "database-restore-separation-confirmation-required"
[[ -d "${APP_ROOT}/.git" ]] || fail "missing-git-checkout"
[[ -f "$ENV_FILE" ]] || fail "missing-staging-env"
[[ "$(stat -c '%a' "$ENV_FILE")" == '600' ]] || fail "staging-env-mode-must-be-600"

for tool in git docker curl; do
  command -v "$tool" >/dev/null 2>&1 || fail "missing-required-tool"
done

cd "$APP_ROOT"
[[ -z "$(git status --porcelain)" ]] || fail "checkout-dirty"
git cat-file -e "${ROLLBACK_COMMIT_SHA}^{commit}" 2>/dev/null || fail "rollback-commit-not-present-locally"

WORKSPACE_COUNT="$(
  docker ps -a \
    --filter 'label=com.inzozi.code.workspace=true' \
    --format '{{.ID}}' \
    </dev/null |
  wc -l |
  tr -d ' '
)"

[[ "$WORKSPACE_COUNT" == '0' ]] || fail "active-workspace-residue-must-be-cleared-before-rollback"

printf '%s\n' 'ISSUE39_PRIVATE_ROLLBACK_START=YES'
printf '%s\n' 'PUBLIC_DNS_DISABLED_OPERATOR_CONFIRMATION=YES'
printf '%s\n' 'PRIVATE_FIREWALL_OPERATOR_CONFIRMATION=YES'
printf '%s\n' 'DATABASE_RESTORE_REQUESTED=NO'
printf '%s\n' 'ACTIVE_WORKSPACE_COUNT=0'
printf '%s\n' 'GIT_RESET_HARD_USED=NO'

START_EPOCH="$(date +%s)"
CURRENT_SHA="$(git rev-parse HEAD)"

if [[ "$CURRENT_SHA" != "$ROLLBACK_COMMIT_SHA" ]]; then
  git switch --detach "$ROLLBACK_COMMIT_SHA"
fi

[[ "$(git rev-parse HEAD)" == "$ROLLBACK_COMMIT_SHA" ]] || fail "rollback-checkout-failed"
[[ -z "$(git status --porcelain)" ]] || fail "rollback-checkout-dirty"

EXPECTED_COMMIT_SHA="$ROLLBACK_COMMIT_SHA" \
  infrastructure/staging/deploy-staging.sh

EXPECTED_COMMIT_SHA="$ROLLBACK_COMMIT_SHA" \
MODE=https \
  infrastructure/staging/configure-host-nginx.sh

COMPOSE=(
  docker compose
  --env-file "$ENV_FILE"
  -f docker-compose.yml
  -f infrastructure/staging/docker-compose.staging.yml
)

"${COMPOSE[@]}" \
  exec \
  -T \
  api \
  python \
  -c '
from app.core.config import get_settings

s = get_settings()
assert s.auth_enabled
assert s.auth_identity_mode == "database"
assert s.auth_cookie_secure
assert s.workspace_ownership_enforced
assert not s.auth_bootstrap_email
assert not s.auth_password_hash
print("DATABASE_IDENTITY_MODE=PASS")
print("BOOTSTRAP_IDENTITY_ABSENT=PASS")
' \
  </dev/null

HEALTH="$(
  curl \
    --silent \
    --output /dev/null \
    --write-out '%{http_code}' \
    http://127.0.0.1:8000/health
)"

READY="$(
  curl \
    --silent \
    --output /dev/null \
    --write-out '%{http_code}' \
    http://127.0.0.1:8000/ready
)"

[[ "$HEALTH" == '200' ]] || fail "health-not-200-after-rollback"
[[ "$READY" == '200' ]] || fail "ready-not-200-after-rollback"
[[ "$(git rev-parse HEAD)" == "$ROLLBACK_COMMIT_SHA" ]] || fail "rollback-sha-drift"
[[ -z "$(git status --porcelain)" ]] || fail "rollback-checkout-dirty-after-deploy"

END_EPOCH="$(date +%s)"
DURATION="$((END_EPOCH - START_EPOCH))"

printf '%s\n' 'PRIVATE_APPLICATION_ROLLBACK=PASS'
printf '%s\n' 'PRIVATE_HTTPS_EDGE_RESTORED=PASS'
printf '%s\n' 'DATABASE_RESTORE_PERFORMED=NO'
printf '%s\n' 'DATABASE_IDENTITY_MODE=PASS'
printf '%s\n' 'BOOTSTRAP_IDENTITY_ABSENT=PASS'
printf '%s\n' 'HEALTH=PASS'
printf '%s\n' 'READY=PASS'
printf 'ROLLBACK_DURATION_SECONDS=%s\n' "$DURATION"
printf '%s\n' 'ROLLBACK_RTO_TARGET_MINUTES=30'
printf '%s\n' 'FIREWALL_MUTATION_BY_THIS_SCRIPT=NO'
printf '%s\n' 'DNS_MUTATION_BY_THIS_SCRIPT=NO'
printf '%s\n' 'PRODUCTION_CHANGE=NO'
printf '%s\n' 'ISSUE39_PRIVATE_STAGING_ROLLBACK=PASS'
