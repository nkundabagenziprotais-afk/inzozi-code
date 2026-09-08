#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIRM_VALUE="CREATE_HETZNER_STAGING"

if ! command -v terraform >/dev/null 2>&1; then
  echo "Terraform is required. Install Terraform 1.16.x before creating staging." >&2
  exit 1
fi

if [[ -z "${HCLOUD_TOKEN:-}" ]]; then
  echo "HCLOUD_TOKEN is required and must be supplied only through the process environment." >&2
  exit 1
fi

if [[ -z "${TF_VAR_ssh_public_key:-}" ]]; then
  echo "TF_VAR_ssh_public_key is required. Supply an SSH public key, never a private key." >&2
  exit 1
fi

if [[ -z "${TF_VAR_ssh_source_cidrs:-}" ]]; then
  echo 'TF_VAR_ssh_source_cidrs is required, for example: ["203.0.113.10/32"]' >&2
  exit 1
fi

if [[ "${INZOZI_CONFIRM_CREATE:-}" != "${CONFIRM_VALUE}" ]]; then
  cat >&2 <<EOF
Refusing to create billable infrastructure without explicit confirmation.

Review the current Hetzner price for the selected server type first, then run with:
  INZOZI_CONFIRM_CREATE=${CONFIRM_VALUE}

The default is a disposable CX33 staging server. Destroy it when review is complete so server billing stops.
EOF
  exit 2
fi

terraform -chdir="${HERE}" init -input=false
terraform -chdir="${HERE}" fmt -check
terraform -chdir="${HERE}" validate
terraform -chdir="${HERE}" plan -input=false -out=staging.tfplan
terraform -chdir="${HERE}" apply -input=false staging.tfplan
rm -f "${HERE}/staging.tfplan"

cat <<'EOF'

Hetzner staging host created.
Next steps:
1. Wait for cloud-init to finish.
2. Run infrastructure/hetzner/bootstrap.sh.
3. Keep public DNS disabled while staging remains private.
4. Deploy the reviewed application commit.
5. Run infrastructure/hetzner/down.sh when review is complete.
EOF

terraform -chdir="${HERE}" output
