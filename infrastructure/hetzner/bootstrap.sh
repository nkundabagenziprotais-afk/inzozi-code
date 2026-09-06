#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${HERE}/../.." && pwd)"
STAGING_BOOTSTRAP="${REPO_ROOT}/infrastructure/staging/bootstrap-host.sh"
ADMIN_USER="${TF_VAR_admin_user:-inzozi}"

if ! command -v terraform >/dev/null 2>&1; then
  echo "Terraform is required to read the staging host address." >&2
  exit 1
fi

if [[ ! -f "${STAGING_BOOTSTRAP}" ]]; then
  echo "Missing provider-neutral staging bootstrap: ${STAGING_BOOTSTRAP}" >&2
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

echo "Waiting for cloud-init on ${TARGET}..."
ssh "${SSH_ARGS[@]}" "${TARGET}" 'cloud-init status --wait'

scp "${SSH_ARGS[@]}" "${STAGING_BOOTSTRAP}" "${TARGET}:/tmp/inzozi-bootstrap-host.sh"
ssh "${SSH_ARGS[@]}" "${TARGET}" \
  'sudo bash /tmp/inzozi-bootstrap-host.sh && rm -f /tmp/inzozi-bootstrap-host.sh'

ssh "${SSH_ARGS[@]}" "${TARGET}" \
  'test -f /etc/inzozi-code-staging && docker --version && docker compose version && sudo nginx -t'

echo "Hetzner staging base host verified: ${TARGET}"
