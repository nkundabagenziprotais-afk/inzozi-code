#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="${APP_ROOT:-/srv/inzozi-code/application}"
ENV_FILE="${ENV_FILE:-/srv/inzozi-code/.env.staging}"
DOMAIN="${DOMAIN:-code-staging.inzozidigital.com}"
EXPECTED_COMMIT_SHA="${EXPECTED_COMMIT_SHA:-}"

TLS_DIR="/etc/inzozi-code/tls/${DOMAIN}"
CA_CERT="${TLS_DIR}/ca.pem"
CERT="${TLS_DIR}/cert.pem"
PRIVATE_KEY="${TLS_DIR}/privkey.pem"

fail() {
  echo "HTTPS preflight failed: $1" >&2
  exit "${2:-1}"
}

[[ -d "${APP_ROOT}/.git" ]] ||
  fail "expected reviewed checkout at ${APP_ROOT}"

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
  fail "missing staging environment file"

[[ "$(stat -c '%a' "${ENV_FILE}")" == "600" ]] ||
  fail "${ENV_FILE} must have mode 600"

COOKIE_SECURE="$(
  grep -E '^AUTH_COOKIE_SECURE=' "${ENV_FILE}" |
    tail -n 1 |
    cut -d= -f2- |
    tr -d "'\"[:space:]"
)"

[[ "${COOKIE_SECURE}" == "true" ]] ||
  fail "AUTH_COOKIE_SECURE must be true"

for path in "${CA_CERT}" "${CERT}" "${PRIVATE_KEY}"; do
  sudo -n test -r "${path}" ||
    fail "TLS material unavailable to Nginx root process: ${path}"
done

sudo -n nginx -t

openssl verify \
  -CAfile "${CA_CERT}" \
  "${CERT}" >/dev/null ||
  fail "certificate chain verification failed"

openssl x509 \
  -checkend 86400 \
  -noout \
  -in "${CERT}" ||
  fail "certificate expires within 24 hours"

openssl x509 \
  -in "${CERT}" \
  -noout \
  -ext subjectAltName |
  grep -Fq "DNS:${DOMAIN}" ||
  fail "certificate SAN does not contain staging domain"

if ! ss -ltnH |
  awk '{print $4}' |
  grep -Eq ':443$'
then
  fail "host nginx is not listening on 443"
fi

RAW_HTTP="$(
  curl \
    --silent \
    --show-error \
    --max-time 8 \
    --header 'Host: unexpected.invalid' \
    --output /dev/null \
    --write-out '%{http_code}' \
    http://127.0.0.1/health
)"

[[ "${RAW_HTTP}" == "404" ]] ||
  fail "unknown HTTP host must return 404"

HTTP_STATUS="$(
  curl \
    --silent \
    --show-error \
    --max-time 8 \
    --header "Host: ${DOMAIN}" \
    --output /dev/null \
    --write-out '%{http_code}' \
    http://127.0.0.1/health
)"

[[ "${HTTP_STATUS}" == "308" ]] ||
  fail "staging HTTP must redirect to HTTPS"

UNKNOWN_HTTPS="$(
  curl \
    --silent \
    --show-error \
    --insecure \
    --max-time 8 \
    --resolve "unexpected.invalid:443:127.0.0.1" \
    --output /dev/null \
    --write-out '%{http_code}' \
    https://unexpected.invalid/health
)"

[[ "${UNKNOWN_HTTPS}" == "404" ]] ||
  fail "unknown HTTPS host must return 404"

HEADERS="$(
  curl \
    --silent \
    --show-error \
    --cacert "${CA_CERT}" \
    --max-time 8 \
    --resolve "${DOMAIN}:443:127.0.0.1" \
    --dump-header - \
    --output /dev/null \
    "https://${DOMAIN}/health"
)"

printf '%s\n' "${HEADERS}" |
  grep -Eiq '^Strict-Transport-Security:[[:space:]]*max-age='

printf '%s\n' "${HEADERS}" |
  grep -Eiq '^X-Content-Type-Options:[[:space:]]*nosniff'

printf '%s\n' "${HEADERS}" |
  grep -Eiq '^X-Frame-Options:[[:space:]]*SAMEORIGIN'

printf '%s\n' "${HEADERS}" |
  grep -Eiq '^Referrer-Policy:[[:space:]]*strict-origin-when-cross-origin'

printf '%s\n' "${HEADERS}" |
  grep -Eiq '^Permissions-Policy:[[:space:]]*camera=\(\), microphone=\(\), geolocation=\(\)'

HTTPS_STATUS="$(
  curl \
    --silent \
    --show-error \
    --cacert "${CA_CERT}" \
    --max-time 8 \
    --resolve "${DOMAIN}:443:127.0.0.1" \
    --output /dev/null \
    --write-out '%{http_code}' \
    "https://${DOMAIN}/health"
)"

[[ "${HTTPS_STATUS}" == "200" ]] ||
  fail "HTTPS health must return 200"

APP_ROOT="${APP_ROOT}" \
ENV_FILE="${ENV_FILE}" \
REQUIRE_SECURE_COOKIE=true \
bash "${APP_ROOT}/infrastructure/staging/auth-preflight.sh"

echo "reviewed_commit=${CURRENT_COMMIT_SHA}"
echo "PRIVATE_CA_CERTIFICATE_VALID=PASS"
echo "HTTPS_LISTENER=PASS"
echo "UNKNOWN_HOST_REJECTION=PASS"
echo "HTTP_TO_HTTPS_REDIRECT=PASS"
echo "HTTPS_HEALTH=PASS"
echo "SECURITY_HEADERS=PASS"
echo "AUTH_COOKIE_SECURE=PASS"
echo "PUBLIC_DNS_REQUIRED=NO"
echo "HTTPS_PREFLIGHT=PASS"
