#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ADMIN_USER="${TF_VAR_admin_user:-inzozi}"
REMOTE_ENV_FILE="${REMOTE_ENV_FILE:-/srv/inzozi-code/.env.staging}"
AUTH_ORGANIZATION_ID="${AUTH_ORGANIZATION_ID:-inzozi-digital}"
AUTH_BOOTSTRAP_ROLE="${AUTH_BOOTSTRAP_ROLE:-platform_owner}"
AUTH_SESSION_TTL_MINUTES="${AUTH_SESSION_TTL_MINUTES:-480}"
AUTH_COOKIE_SECURE="${AUTH_COOKIE_SECURE:-false}"

if [[ ! "${AUTH_SESSION_TTL_MINUTES}" =~ ^[0-9]+$ ]] || (( AUTH_SESSION_TTL_MINUTES < 15 || AUTH_SESSION_TTL_MINUTES > 1440 )); then
  echo "AUTH_SESSION_TTL_MINUTES must be between 15 and 1440." >&2
  exit 1
fi
if [[ "${AUTH_COOKIE_SECURE}" != "true" && "${AUTH_COOKIE_SECURE}" != "false" ]]; then
  echo "AUTH_COOKIE_SECURE must be true or false." >&2
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

LOCAL_FRAGMENT="$(mktemp -t inzozi-auth.XXXXXX)"
REMOTE_FRAGMENT="/tmp/inzozi-auth-${RANDOM}-${RANDOM}.env"
cleanup() {
  rm -f "${LOCAL_FRAGMENT}"
  ssh "${SSH_ARGS[@]}" "${TARGET}" "rm -f '${REMOTE_FRAGMENT}'" >/dev/null 2>&1 || true
}
trap cleanup EXIT
chmod 0600 "${LOCAL_FRAGMENT}"

AUTH_VALUES="$(python3 - <<'PY'
import base64
import getpass
import hashlib
import re
import secrets

email = input("Staging owner email: ").strip().casefold()
if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
    raise SystemExit("A valid email address is required.")
password = getpass.getpass("Staging owner password (minimum 12 characters): ")
confirm = getpass.getpass("Confirm staging owner password: ")
if password != confirm:
    raise SystemExit("Passwords do not match.")
if len(password) < 12:
    raise SystemExit("Password must contain at least 12 characters.")
salt = secrets.token_bytes(16)
iterations = 600_000
digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations, dklen=32)
enc = lambda value: base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")
password_hash = f"pbkdf2_sha256${iterations}${enc(salt)}${enc(digest)}"
session_secret = secrets.token_hex(32)
print(email)
print(password_hash)
print(session_secret)
PY
)"

AUTH_EMAIL="$(printf '%s\n' "${AUTH_VALUES}" | sed -n '1p')"
AUTH_PASSWORD_HASH="$(printf '%s\n' "${AUTH_VALUES}" | sed -n '2p')"
AUTH_SESSION_SECRET="$(printf '%s\n' "${AUTH_VALUES}" | sed -n '3p')"
unset AUTH_VALUES

if [[ -z "${AUTH_EMAIL}" || -z "${AUTH_PASSWORD_HASH}" || ${#AUTH_SESSION_SECRET} -lt 64 ]]; then
  echo "Unable to generate staging authentication material." >&2
  exit 1
fi

cat >"${LOCAL_FRAGMENT}" <<EOF
AUTH_ENABLED=true
AUTH_BOOTSTRAP_EMAIL=${AUTH_EMAIL}
AUTH_PASSWORD_HASH=${AUTH_PASSWORD_HASH}
AUTH_SESSION_SECRET=${AUTH_SESSION_SECRET}
AUTH_BOOTSTRAP_ROLE=${AUTH_BOOTSTRAP_ROLE}
AUTH_ORGANIZATION_ID=${AUTH_ORGANIZATION_ID}
AUTH_SESSION_TTL_MINUTES=${AUTH_SESSION_TTL_MINUTES}
AUTH_COOKIE_SECURE=${AUTH_COOKIE_SECURE}
EOF
unset AUTH_PASSWORD_HASH AUTH_SESSION_SECRET

scp "${SSH_ARGS[@]}" "${LOCAL_FRAGMENT}" "${TARGET}:${REMOTE_FRAGMENT}" >/dev/null

ssh "${SSH_ARGS[@]}" "${TARGET}" bash -s -- "${REMOTE_ENV_FILE}" "${REMOTE_FRAGMENT}" <<'REMOTE'
set -euo pipefail
env_file="$1"
fragment="$2"

if [[ ! -f "${env_file}" ]]; then
  echo "Missing staging environment file: ${env_file}" >&2
  exit 1
fi
if [[ "$(stat -c '%a' "${env_file}")" != "600" ]]; then
  echo "Refusing authentication configuration: ${env_file} must have mode 600." >&2
  exit 1
fi
if [[ ! -f "${fragment}" ]]; then
  echo "Authentication fragment was not uploaded." >&2
  exit 1
fi
chmod 0600 "${fragment}"

python3 - "${env_file}" "${fragment}" <<'PY'
from pathlib import Path
import os
import sys

env_path = Path(sys.argv[1])
fragment_path = Path(sys.argv[2])
updates = {}
for raw in fragment_path.read_text(encoding="utf-8").splitlines():
    if not raw or "=" not in raw:
        continue
    key, value = raw.split("=", 1)
    updates[key] = value

required = {
    "AUTH_ENABLED",
    "AUTH_BOOTSTRAP_EMAIL",
    "AUTH_PASSWORD_HASH",
    "AUTH_SESSION_SECRET",
    "AUTH_BOOTSTRAP_ROLE",
    "AUTH_ORGANIZATION_ID",
    "AUTH_SESSION_TTL_MINUTES",
    "AUTH_COOKIE_SECURE",
}
missing = sorted(required - updates.keys())
if missing:
    raise SystemExit("Authentication fragment is incomplete: " + ", ".join(missing))

lines = env_path.read_text(encoding="utf-8").splitlines()
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

tmp = env_path.with_name(env_path.name + ".tmp-auth")
tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
os.chmod(tmp, 0o600)
os.replace(tmp, env_path)
PY

rm -f "${fragment}"
chmod 0600 "${env_file}"

if grep -Eq '^AUTH_PASSWORD=' "${env_file}"; then
  echo "Refusing authentication configuration: plaintext AUTH_PASSWORD must not be present." >&2
  exit 1
fi

printf 'Staging authentication configuration written without storing a plaintext password.\n'
printf 'auth_enabled=true role=%s cookie_secure=%s\n' \
  "$(grep -E '^AUTH_BOOTSTRAP_ROLE=' "${env_file}" | cut -d= -f2-)" \
  "$(grep -E '^AUTH_COOKIE_SECURE=' "${env_file}" | cut -d= -f2-)"
REMOTE

trap - EXIT
rm -f "${LOCAL_FRAGMENT}"
printf 'Staging authentication secrets were transferred over SSH and were not printed.\n'
printf 'Next: deploy the reviewed auth branch, then run infrastructure/staging/auth-preflight.sh.\n'
