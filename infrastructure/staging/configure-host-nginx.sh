#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="${APP_ROOT:-/srv/inzozi-code/application}"
ENV_FILE="${ENV_FILE:-/srv/inzozi-code/.env.staging}"
MODE="${MODE:-http}"
EXPECTED_COMMIT_SHA="${EXPECTED_COMMIT_SHA:-}"

DOMAIN="code-staging.inzozidigital.com"
SITE_NAME="inzozi-code-staging"

AVAILABLE="/etc/nginx/sites-available/${SITE_NAME}"
ENABLED="/etc/nginx/sites-enabled/${SITE_NAME}"
DEFAULT_ENABLED="/etc/nginx/sites-enabled/default"

HTTP_TEMPLATE="${APP_ROOT}/infrastructure/staging/host-nginx-http.conf.template"
HTTPS_TEMPLATE="${APP_ROOT}/infrastructure/staging/host-nginx.conf.template"

TLS_DIR="/etc/inzozi-code/tls/${DOMAIN}"
CA_CERT="${TLS_DIR}/ca.pem"
CERT="${TLS_DIR}/cert.pem"
FULLCHAIN="${TLS_DIR}/fullchain.pem"
PRIVATE_KEY="${TLS_DIR}/privkey.pem"

fail() {
  echo "Host Nginx configuration failed: $1" >&2
  exit "${2:-1}"
}

if [[ "${EUID}" -ne 0 ]]; then
  fail "run this script as root or through sudo"
fi

case "${MODE}" in
  http|https)
    ;;
  *)
    fail "MODE must be http or https"
    ;;
esac

[[ -d "${APP_ROOT}/.git" ]] ||
  fail "expected reviewed application checkout at ${APP_ROOT}"

[[ "${EXPECTED_COMMIT_SHA}" =~ ^[0-9a-f]{40}$ ]] ||
  fail "EXPECTED_COMMIT_SHA must be the exact reviewed 40-character commit SHA"

[[ -z "$(git -C "${APP_ROOT}" status --porcelain)" ]] ||
  fail "reviewed application checkout must be clean"

CURRENT_COMMIT_SHA="$(
  git -C "${APP_ROOT}" rev-parse HEAD
)"

[[ "${CURRENT_COMMIT_SHA}" == "${EXPECTED_COMMIT_SHA}" ]] ||
  fail "application checkout does not match EXPECTED_COMMIT_SHA"

[[ -f "${ENV_FILE}" ]] ||
  fail "missing staging environment file: ${ENV_FILE}"

[[ "$(stat -c '%a' "${ENV_FILE}")" == "600" ]] ||
  fail "${ENV_FILE} must have mode 600"

command -v nginx >/dev/null 2>&1 ||
  fail "host nginx binary is unavailable"

command -v systemctl >/dev/null 2>&1 ||
  fail "systemctl is unavailable"

UPSTREAM="$(
  curl \
    --silent \
    --show-error \
    --max-time 5 \
    --output /dev/null \
    --write-out '%{http_code}' \
    http://127.0.0.1:8080/health
)"

[[ "${UPSTREAM}" == "200" ]] ||
  fail "private Docker nginx upstream is not healthy"

if [[ "${MODE}" == "http" ]]; then
  TEMPLATE="${HTTP_TEMPLATE}"
else
  TEMPLATE="${HTTPS_TEMPLATE}"

  for path in \
    "${CA_CERT}" \
    "${CERT}" \
    "${FULLCHAIN}" \
    "${PRIVATE_KEY}"
  do
    [[ -r "${path}" ]] ||
      fail "HTTPS activation requires ${path}"
  done

  openssl verify \
    -CAfile "${CA_CERT}" \
    "${CERT}" >/dev/null ||
    fail "private staging certificate verification failed"

  COOKIE_SECURE="$(
    grep -E '^AUTH_COOKIE_SECURE=' "${ENV_FILE}" |
      tail -n 1 |
      cut -d= -f2- |
      tr -d "'\"[:space:]"
  )"

  [[ "${COOKIE_SECURE}" == "true" ]] ||
    fail "AUTH_COOKIE_SECURE must be true before HTTPS activation"

  # Verify the RUNNING API has loaded the secure-cookie setting before
  # changing host ingress.
  APP_ROOT="${APP_ROOT}" \
  ENV_FILE="${ENV_FILE}" \
  REQUIRE_SECURE_COOKIE=true \
  bash "${APP_ROOT}/infrastructure/staging/auth-preflight.sh"
fi

[[ -f "${TEMPLATE}" ]] ||
  fail "missing host nginx template: ${TEMPLATE}"

install -d -m 0755 /etc/nginx/sites-available
install -d -m 0755 /etc/nginx/sites-enabled

if [[ -e "${ENABLED}" && ! -L "${ENABLED}" ]]; then
  fail "${ENABLED} exists but is not a symlink"
fi

if [[ -e "${DEFAULT_ENABLED}" &&
      ! -L "${DEFAULT_ENABLED}" ]]; then
  fail "${DEFAULT_ENABLED} exists but is not a symlink"
fi

OLD_SITE_TARGET=""
OLD_DEFAULT_TARGET=""

if [[ -L "${ENABLED}" ]]; then
  OLD_SITE_TARGET="$(readlink "${ENABLED}")"
fi

if [[ -L "${DEFAULT_ENABLED}" ]]; then
  OLD_DEFAULT_TARGET="$(readlink "${DEFAULT_ENABLED}")"
fi

AVAILABLE_BACKUP=""

if [[ -f "${AVAILABLE}" ]]; then
  AVAILABLE_BACKUP="$(
    mktemp \
      /etc/nginx/sites-available/.inzozi-code-staging.backup.XXXXXX
  )"

  cp -- "${AVAILABLE}" "${AVAILABLE_BACKUP}"
fi

CANDIDATE="$(
  mktemp \
    /etc/nginx/sites-available/.inzozi-code-staging.candidate.XXXXXX
)"

cleanup_files() {
  rm -f \
    "${CANDIDATE:-}" \
    "${AVAILABLE_BACKUP:-}" \
    >/dev/null 2>&1 || true
}
trap cleanup_files EXIT

rollback() {
  set +e

  if [[ -n "${AVAILABLE_BACKUP}" &&
        -f "${AVAILABLE_BACKUP}" ]]; then
    cp -- "${AVAILABLE_BACKUP}" "${AVAILABLE}"
  else
    rm -f "${AVAILABLE}"
  fi

  if [[ -n "${OLD_SITE_TARGET}" ]]; then
    ln -sfn "${OLD_SITE_TARGET}" "${ENABLED}"
  else
    rm -f "${ENABLED}"
  fi

  if [[ -n "${OLD_DEFAULT_TARGET}" ]]; then
    ln -sfn "${OLD_DEFAULT_TARGET}" "${DEFAULT_ENABLED}"
  else
    rm -f "${DEFAULT_ENABLED}"
  fi

  nginx -t >/dev/null 2>&1 || true
}

install -m 0644 "${TEMPLATE}" "${CANDIDATE}"
install -m 0644 "${CANDIDATE}" "${AVAILABLE}"
ln -sfn "${AVAILABLE}" "${ENABLED}"

# Ubuntu's stock site is itself a port-80 default_server. Remove its
# enabled symlink before validating our candidate so two default servers
# are never tested together. The running Nginx configuration is unchanged
# until reload, and rollback restores the previous symlink exactly.
rm -f "${DEFAULT_ENABLED}"

if ! nginx -t; then
  rollback
  fail "candidate configuration failed after controlled default-site removal"
fi

if ! systemctl reload nginx; then
  rollback
  systemctl reload nginx >/dev/null 2>&1 || true
  fail "nginx reload failed; previous configuration restored"
fi

FAILED=0

RAW_HTTP="$(
  curl \
    --silent \
    --show-error \
    --max-time 8 \
    --header 'Host: unexpected.invalid' \
    --output /dev/null \
    --write-out '%{http_code}' \
    http://127.0.0.1/health ||
    true
)"

[[ "${RAW_HTTP}" == "404" ]] || FAILED=1

if [[ "${MODE}" == "http" ]]; then

  DOMAIN_HTTP="$(
    curl \
      --silent \
      --show-error \
      --max-time 8 \
      --header "Host: ${DOMAIN}" \
      --output /dev/null \
      --write-out '%{http_code}' \
      http://127.0.0.1/health ||
      true
  )"

  [[ "${DOMAIN_HTTP}" == "200" ]] || FAILED=1

else

  DOMAIN_HTTP="$(
    curl \
      --silent \
      --show-error \
      --max-time 8 \
      --header "Host: ${DOMAIN}" \
      --output /dev/null \
      --write-out '%{http_code}' \
      http://127.0.0.1/health ||
      true
  )"

  [[ "${DOMAIN_HTTP}" == "308" ]] || FAILED=1

  UNKNOWN_HTTPS="$(
    curl \
      --silent \
      --show-error \
      --insecure \
      --max-time 8 \
      --resolve "unexpected.invalid:443:127.0.0.1" \
      --output /dev/null \
      --write-out '%{http_code}' \
      https://unexpected.invalid/health ||
      true
  )"

  [[ "${UNKNOWN_HTTPS}" == "404" ]] || FAILED=1

  DOMAIN_HTTPS="$(
    curl \
      --silent \
      --show-error \
      --cacert "${CA_CERT}" \
      --max-time 8 \
      --resolve "${DOMAIN}:443:127.0.0.1" \
      --output /dev/null \
      --write-out '%{http_code}' \
      "https://${DOMAIN}/health" ||
      true
  )"

  [[ "${DOMAIN_HTTPS}" == "200" ]] || FAILED=1
fi

if [[ "${FAILED}" != "0" ]]; then
  rollback

  if ! systemctl reload nginx; then
    echo "WARNING: rollback reload also failed." >&2
  fi

  fail "functional validation failed; previous configuration restored"
fi

cleanup_files
trap - EXIT

echo "HOST_NGINX_CONFIGURATION_MODE=${MODE}"
echo "reviewed_commit=${CURRENT_COMMIT_SHA}"
echo "DEFAULT_VHOST_REJECTS_UNKNOWN_HOSTS=PASS"
echo "HOST_NGINX_FUNCTIONAL_CHECK=PASS"
echo "NO_DNS_CHANGE"
echo "NO_FIREWALL_CHANGE"
