#!/usr/bin/env bash
set -euo pipefail

: "${GITHUB_APP_ID:?Set GITHUB_APP_ID to the numeric GitHub App ID}"
: "${GITHUB_APP_PRIVATE_KEY_FILE:?Set GITHUB_APP_PRIVATE_KEY_FILE to the downloaded PEM path}"

GITHUB_REPOSITORY_OWNER="${GITHUB_REPOSITORY_OWNER:-nkundabagenziprotais-afk}"
GITHUB_REPOSITORY_NAME="${GITHUB_REPOSITORY_NAME:-inzozi-code}"
GITHUB_API_VERSION="2026-03-10"

if [[ ! "${GITHUB_APP_ID}" =~ ^[0-9]+$ ]]; then
  echo "GITHUB_APP_ID must be numeric." >&2
  exit 1
fi
if [[ ! -f "${GITHUB_APP_PRIVATE_KEY_FILE}" ]]; then
  echo "GitHub App private key file was not found: ${GITHUB_APP_PRIVATE_KEY_FILE}" >&2
  exit 1
fi
if ! head -n 1 "${GITHUB_APP_PRIVATE_KEY_FILE}" | grep -Eq '^-----BEGIN (RSA )?PRIVATE KEY-----$'; then
  echo "The selected file does not look like a GitHub App private-key PEM." >&2
  exit 1
fi
if [[ ! "${GITHUB_REPOSITORY_OWNER}" =~ ^[A-Za-z0-9][A-Za-z0-9-]{0,38}$ ]]; then
  echo "GITHUB_REPOSITORY_OWNER is invalid." >&2
  exit 1
fi
if [[ ! "${GITHUB_REPOSITORY_NAME}" =~ ^[A-Za-z0-9._-]+$ ]]; then
  echo "GITHUB_REPOSITORY_NAME is invalid." >&2
  exit 1
fi

b64url() {
  python3 -c 'import base64,sys; print(base64.urlsafe_b64encode(sys.stdin.buffer.read()).decode().rstrip("="))'
}

now="$(date +%s)"
header='{"alg":"RS256","typ":"JWT"}'
payload="$(printf '{\"iat\":%s,\"exp\":%s,\"iss\":\"%s\"}' "$((now - 30))" "$((now + 540))" "${GITHUB_APP_ID}")"
header64="$(printf '%s' "${header}" | b64url)"
payload64="$(printf '%s' "${payload}" | b64url)"
unsigned="${header64}.${payload64}"
signature64="$(printf '%s' "${unsigned}" | openssl dgst -sha256 -sign "${GITHUB_APP_PRIVATE_KEY_FILE}" | b64url)"
jwt="${unsigned}.${signature64}"

response_file="$(mktemp)"
cleanup() {
  rm -f "${response_file}"
}
trap cleanup EXIT

status="$(curl --silent --show-error --location \
  --output "${response_file}" \
  --write-out '%{http_code}' \
  -H 'Accept: application/vnd.github+json' \
  -H "Authorization: Bearer ${jwt}" \
  -H "X-GitHub-Api-Version: ${GITHUB_API_VERSION}" \
  "https://api.github.com/repos/${GITHUB_REPOSITORY_OWNER}/${GITHUB_REPOSITORY_NAME}/installation")"

if [[ "${status}" != "200" ]]; then
  echo "GitHub could not resolve an installation for ${GITHUB_REPOSITORY_OWNER}/${GITHUB_REPOSITORY_NAME} (HTTP ${status})." >&2
  python3 - "${response_file}" <<'PY' >&2
import json
import sys
from pathlib import Path
try:
    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
except Exception:
    raise SystemExit(0)
message = payload.get("message")
if isinstance(message, str):
    print("GitHub message: " + message)
PY
  echo "Verify that this exact GitHub App is installed on the selected repository and that the App ID/PEM belong to the same App." >&2
  exit 1
fi

python3 - "${response_file}" "${GITHUB_REPOSITORY_OWNER}/${GITHUB_REPOSITORY_NAME}" <<'PY'
import json
import sys
from pathlib import Path
payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
installation_id = payload.get("id")
account = (payload.get("account") or {}).get("login")
selection = payload.get("repository_selection")
if not isinstance(installation_id, int) or installation_id <= 0:
    raise SystemExit("GitHub response did not contain a valid installation id")
print(installation_id)
print(f"Resolved GitHub App installation for {sys.argv[2]} · account={account or 'unknown'} · repository_selection={selection or 'unknown'}", file=sys.stderr)
PY
