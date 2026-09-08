#!/usr/bin/env bash
set -euo pipefail

DOMAIN="${DOMAIN:-code-staging.inzozidigital.com}"
TLS_ROOT="${TLS_ROOT:-/etc/inzozi-code/tls}"
TLS_DIR="${TLS_ROOT}/${DOMAIN}"

CA_CERT="${TLS_DIR}/ca.pem"
CERT="${TLS_DIR}/cert.pem"
FULLCHAIN="${TLS_DIR}/fullchain.pem"
PRIVATE_KEY="${TLS_DIR}/privkey.pem"

fail() {
  echo "Private staging TLS preparation failed: $1" >&2
  exit "${2:-1}"
}

if [[ "${EUID}" -ne 0 ]]; then
  fail "run this script as root or through sudo"
fi

command -v openssl >/dev/null 2>&1 ||
  fail "openssl is unavailable"

install -d -m 0755 "${TLS_ROOT}"

if [[ -d "${TLS_DIR}" ]]; then
  if [[ -r "${CA_CERT}" &&
        -r "${CERT}" &&
        -r "${FULLCHAIN}" &&
        -r "${PRIVATE_KEY}" ]] &&
     openssl verify \
       -CAfile "${CA_CERT}" \
       "${CERT}" >/dev/null 2>&1 &&
     openssl x509 \
       -checkend 604800 \
       -noout \
       -in "${CERT}" >/dev/null 2>&1 &&
     openssl x509 \
       -in "${CERT}" \
       -noout \
       -ext subjectAltName |
       grep -Fq "DNS:${DOMAIN}"
  then
    echo "PRIVATE_STAGING_TLS_EXISTING_VALID=PASS"
    echo "NO_CERTIFICATE_ROTATION"
    exit 0
  fi

  fail "existing TLS directory is incomplete or invalid; refusing automatic overwrite"
fi

TMP="$(
  mktemp -d "${TLS_ROOT}/.private-staging.XXXXXX"
)"

cleanup() {
  rm -rf "${TMP}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

chmod 0700 "${TMP}"

openssl genrsa \
  -out "${TMP}/ca.key" \
  3072 \
  >/dev/null 2>&1

chmod 0600 "${TMP}/ca.key"

openssl req \
  -x509 \
  -new \
  -sha256 \
  -days 30 \
  -key "${TMP}/ca.key" \
  -subj "/CN=Inzozi Private Staging CA" \
  -addext "basicConstraints=critical,CA:TRUE" \
  -addext "keyUsage=critical,keyCertSign,cRLSign" \
  -out "${TMP}/ca.pem" \
  >/dev/null 2>&1

openssl genrsa \
  -out "${TMP}/privkey.pem" \
  2048 \
  >/dev/null 2>&1

chmod 0600 "${TMP}/privkey.pem"

openssl req \
  -new \
  -key "${TMP}/privkey.pem" \
  -subj "/CN=${DOMAIN}" \
  -addext "subjectAltName=DNS:${DOMAIN}" \
  -out "${TMP}/server.csr" \
  >/dev/null 2>&1

cat > "${TMP}/server.ext" <<EXT
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=DNS:${DOMAIN}
EXT

openssl x509 \
  -req \
  -sha256 \
  -days 30 \
  -in "${TMP}/server.csr" \
  -CA "${TMP}/ca.pem" \
  -CAkey "${TMP}/ca.key" \
  -CAcreateserial \
  -extfile "${TMP}/server.ext" \
  -out "${TMP}/cert.pem" \
  >/dev/null 2>&1

cat \
  "${TMP}/cert.pem" \
  "${TMP}/ca.pem" \
  > "${TMP}/fullchain.pem"

openssl verify \
  -CAfile "${TMP}/ca.pem" \
  "${TMP}/cert.pem" \
  >/dev/null

openssl x509 \
  -in "${TMP}/cert.pem" \
  -noout \
  -ext subjectAltName |
  grep -Fq "DNS:${DOMAIN}"

CERT_KEY_FP="$(
  openssl x509 \
    -in "${TMP}/cert.pem" \
    -pubkey \
    -noout |
  openssl pkey \
    -pubin \
    -outform DER \
    2>/dev/null |
  sha256sum |
  awk '{print $1}'
)"

PRIVATE_KEY_FP="$(
  openssl pkey \
    -in "${TMP}/privkey.pem" \
    -pubout \
    -outform DER \
    2>/dev/null |
  sha256sum |
  awk '{print $1}'
)"

[[ "${CERT_KEY_FP}" == "${PRIVATE_KEY_FP}" ]] ||
  fail "generated certificate and private key do not match"

install -d -m 0755 "${TLS_DIR}"

install -m 0644 "${TMP}/ca.pem" "${CA_CERT}"
install -m 0644 "${TMP}/cert.pem" "${CERT}"
install -m 0644 "${TMP}/fullchain.pem" "${FULLCHAIN}"
install -m 0600 "${TMP}/privkey.pem" "${PRIVATE_KEY}"

chown root:root \
  "${CA_CERT}" \
  "${CERT}" \
  "${FULLCHAIN}" \
  "${PRIVATE_KEY}"

# The private CA signing key is deliberately not persisted.
trap - EXIT
rm -rf "${TMP}"

echo "PRIVATE_STAGING_CA_CREATED=PASS"
echo "PRIVATE_STAGING_SERVER_CERT_CREATED=PASS"
echo "PRIVATE_CA_SIGNING_KEY_PERSISTED=NO"
echo "PUBLIC_CA_CERTIFICATE=NO"
echo "PUBLIC_DNS_REQUIRED=NO"
