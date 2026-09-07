#!/usr/bin/env bash
set -euo pipefail

KEY_PATH="${SSH_KEY_PATH:-${HOME}/.ssh/inzozi_code_staging_ed25519}"
KEY_DIR="$(dirname "${KEY_PATH}")"
KEY_COMMENT="${SSH_KEY_COMMENT:-inzozi-code-staging}"

for command_name in ssh-keygen curl; do
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "Missing required command: ${command_name}" >&2
    exit 1
  fi
done

install -d -m 0700 "${KEY_DIR}"

if [[ -e "${KEY_PATH}" || -e "${KEY_PATH}.pub" ]]; then
  if [[ ! -f "${KEY_PATH}" || ! -f "${KEY_PATH}.pub" ]]; then
    echo "Refusing to continue because only one half of the SSH key pair exists at ${KEY_PATH}." >&2
    exit 1
  fi
  echo "Reusing existing staging SSH key: ${KEY_PATH}"
else
  echo "Creating a dedicated Ed25519 SSH key for Inzozi Code staging."
  echo "ssh-keygen will ask whether you want a passphrase. A passphrase is recommended for an operator key."
  ssh-keygen -t ed25519 -a 64 -C "${KEY_COMMENT}" -f "${KEY_PATH}"
fi

chmod 0600 "${KEY_PATH}"
chmod 0644 "${KEY_PATH}.pub"

PUBLIC_IPV4="$(curl -4 --fail --silent --show-error --max-time 10 https://api.ipify.org)"

if [[ ! "${PUBLIC_IPV4}" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
  echo "Unable to determine a valid public IPv4 address for the SSH firewall rule." >&2
  exit 1
fi

IFS='.' read -r octet1 octet2 octet3 octet4 <<<"${PUBLIC_IPV4}"
for octet in "${octet1}" "${octet2}" "${octet3}" "${octet4}"; do
  if (( octet < 0 || octet > 255 )); then
    echo "Detected invalid public IPv4 address: ${PUBLIC_IPV4}" >&2
    exit 1
  fi
done

cat <<EOF

Local Hetzner staging prerequisites are ready.

SSH private key: ${KEY_PATH}
SSH public key:  ${KEY_PATH}.pub
Detected SSH source: ${PUBLIC_IPV4}/32

For this terminal session, run:

  export HCLOUD_TOKEN='YOUR_HETZNER_PROJECT_TOKEN'
  export SSH_KEY_PATH='${KEY_PATH}'
  export TF_VAR_ssh_public_key="\$(cat '${KEY_PATH}.pub')"
  export TF_VAR_ssh_source_cidrs='["${PUBLIC_IPV4}/32"]'

Security boundary:
- Enter HCLOUD_TOKEN only in your own terminal or secret manager.
- Never paste the token, SSH private key, or staging application secrets into Git or chat.
- If your public IP changes before provisioning, run this script again to refresh the /32 firewall value.

When the variables are set, create staging with:

  INZOZI_CONFIRM_CREATE=CREATE_HETZNER_STAGING ./infrastructure/hetzner/up.sh
EOF
