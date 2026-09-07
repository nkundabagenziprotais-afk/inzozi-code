#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ADMIN_USER="${TF_VAR_admin_user:-inzozi}"
REMOTE_ENV_FILE="${REMOTE_ENV_FILE:-/srv/inzozi-code/.env.staging}"

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

ssh "${SSH_ARGS[@]}" "${TARGET}" bash -s -- "${REMOTE_ENV_FILE}" <<'REMOTE'
set -euo pipefail
env_file="$1"

if [[ ! -f "${env_file}" ]]; then
  echo "Missing staging environment file: ${env_file}" >&2
  exit 1
fi
if [[ "$(stat -c '%a' "${env_file}")" != "600" ]]; then
  echo "Refusing broker configuration: ${env_file} must have mode 600." >&2
  exit 1
fi

python3 - "${env_file}" <<'PY'
from pathlib import Path
import os
import secrets
import sys

env_path = Path(sys.argv[1])
lines = env_path.read_text(encoding="utf-8").splitlines()
key = "WORKSPACE_BROKER_TOKEN"
existing = ""
for line in lines:
    if line.startswith(key + "="):
        existing = line.split("=", 1)[1].strip().strip("'\"")
        break

if len(existing) >= 32 and existing != "development-only-broker-token-change-me":
    token = existing
    changed = False
else:
    token = secrets.token_urlsafe(48)
    changed = True

out = []
seen = False
for line in lines:
    if line.startswith(key + "="):
        out.append(f"{key}={token}")
        seen = True
    else:
        out.append(line)
if not seen:
    out.append(f"{key}={token}")

tmp = env_path.with_name(env_path.name + ".tmp-broker")
tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
os.chmod(tmp, 0o600)
os.replace(tmp, env_path)
print("created" if changed else "preserved")
PY

chmod 0600 "${env_file}"
if grep -Eq '^WORKSPACE_BROKER_TOKEN=(development-only-broker-token-change-me)?$' "${env_file}"; then
  echo "Workspace broker token configuration failed." >&2
  exit 1
fi

echo "Workspace broker authentication is configured without printing the secret."
REMOTE

printf 'Next: deploy the reviewed control-plane branch, then run the broker preflight.\n'
