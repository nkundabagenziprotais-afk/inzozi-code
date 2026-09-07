#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ADMIN_USER="${TF_VAR_admin_user:-inzozi}"
GITHUB_APP_OWNER="${GITHUB_APP_OWNER:-nkundabagenziprotais-afk}"
REMOTE_ENV_FILE="${REMOTE_ENV_FILE:-/srv/inzozi-code/.env.staging}"
REMOTE_KEY_PATH="${REMOTE_KEY_PATH:-/srv/inzozi-code/secrets/github-app.pem}"

: "${GITHUB_APP_ID:?Set GITHUB_APP_ID to the numeric GitHub App ID}"
: "${GITHUB_APP_INSTALLATION_ID:?Set GITHUB_APP_INSTALLATION_ID to the numeric installation ID}"
: "${GITHUB_APP_PRIVATE_KEY_FILE:?Set GITHUB_APP_PRIVATE_KEY_FILE to the local downloaded PEM path}"

if [[ ! "${GITHUB_APP_ID}" =~ ^[0-9]+$ ]]; then
  echo "GITHUB_APP_ID must be numeric." >&2
  exit 1
fi
if [[ ! "${GITHUB_APP_INSTALLATION_ID}" =~ ^[0-9]+$ ]]; then
  echo "GITHUB_APP_INSTALLATION_ID must be numeric." >&2
  exit 1
fi
if [[ ! "${GITHUB_APP_OWNER}" =~ ^[A-Za-z0-9][A-Za-z0-9-]{0,38}$ ]]; then
  echo "GITHUB_APP_OWNER is not a valid GitHub account name." >&2
  exit 1
fi
if [[ ! -f "${GITHUB_APP_PRIVATE_KEY_FILE}" ]]; then
  echo "GitHub App PEM file was not found: ${GITHUB_APP_PRIVATE_KEY_FILE}" >&2
  exit 1
fi
if ! head -n 1 "${GITHUB_APP_PRIVATE_KEY_FILE}" | grep -Eq '^-----BEGIN (RSA )?PRIVATE KEY-----$'; then
  echo "The selected file does not look like a GitHub App private-key PEM." >&2
  exit 1
fi

IP="$(terraform -chdir="${HERE}" output -raw ipv4_address 2>/dev/null || true)"
if [[ -z "${IP}" ]]; then
  echo "No Hetzner staging IPv4 address found. Create the host first." >&2
  exit 1
fi

SSH_ARGS=(-o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)
if [[ -n "${SSH_KEY_PATH:-}" ]]; then
  SSH_ARGS+=(-i "${SSH_KEY_PATH}")
fi
TARGET="${ADMIN_USER}@${IP}"
REMOTE_TMP="/tmp/inzozi-github-app-${RANDOM}-${RANDOM}.pem"

cleanup_remote() {
  ssh "${SSH_ARGS[@]}" "${TARGET}" "rm -f '${REMOTE_TMP}'" >/dev/null 2>&1 || true
}
trap cleanup_remote EXIT

scp "${SSH_ARGS[@]}" "${GITHUB_APP_PRIVATE_KEY_FILE}" "${TARGET}:${REMOTE_TMP}" >/dev/null

ssh "${SSH_ARGS[@]}" "${TARGET}" bash -s -- \
  "${GITHUB_APP_ID}" \
  "${GITHUB_APP_OWNER}" \
  "${GITHUB_APP_INSTALLATION_ID}" \
  "${REMOTE_TMP}" \
  "${REMOTE_ENV_FILE}" \
  "${REMOTE_KEY_PATH}" <<'REMOTE'
set -euo pipefail

app_id="$1"
owner="$2"
installation_id="$3"
tmp_key="$4"
env_file="$5"
key_path="$6"
key_dir="$(dirname "${key_path}")"

if [[ ! -f "${env_file}" ]]; then
  echo "Missing staging environment file: ${env_file}" >&2
  exit 1
fi
if [[ "$(stat -c '%a' "${env_file}")" != "600" ]]; then
  echo "Refusing GitHub App configuration: ${env_file} must have mode 600." >&2
  exit 1
fi

sudo install -d -o root -g root -m 0700 "${key_dir}"
sudo cp "${tmp_key}" "${key_path}"
sudo chown 10001:10001 "${key_path}"
sudo chmod 0400 "${key_path}"
rm -f "${tmp_key}"

python3 - "${env_file}" "${app_id}" "${owner}" "${installation_id}" "${key_path}" <<'PY'
from pathlib import Path
import os
import sys

path = Path(sys.argv[1])
updates = {
    "GITHUB_APP_ID": sys.argv[2],
    "GITHUB_APP_OWNER": sys.argv[3],
    "GITHUB_APP_DEFAULT_INSTALLATION_ID": sys.argv[4],
    "GITHUB_APP_PRIVATE_KEY_HOST_PATH": sys.argv[5],
}

lines = path.read_text(encoding="utf-8").splitlines()
seen = set()
out = []
for line in lines:
    key = line.split("=", 1)[0] if "=" in line else ""
    if key in updates:
        out.append(f"{key}={updates[key]}")
        seen.add(key)
    else:
        out.append(line)
for key, value in updates.items():
    if key not in seen:
        out.append(f"{key}={value}")

tmp = path.with_name(path.name + ".tmp-github-app")
tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
os.chmod(tmp, 0o600)
os.replace(tmp, path)
PY

chmod 0600 "${env_file}"

mode="$(sudo stat -c '%a' "${key_path}")"
uid="$(sudo stat -c '%u' "${key_path}")"
if [[ "${mode}" != "400" || "${uid}" != "10001" ]]; then
  echo "GitHub App key installation did not meet the expected UID/mode boundary." >&2
  exit 1
fi

printf 'GitHub App staging configuration written safely.\n'
printf 'owner=%s installation_id=%s key_mode=%s key_uid=%s\n' "${owner}" "${installation_id}" "${mode}" "${uid}"
REMOTE

trap - EXIT
printf 'GitHub App key uploaded to staging without printing key material.\n'
printf 'Next: redeploy the reviewed branch, then run infrastructure/staging/github-app-preflight.sh on the host.\n'
