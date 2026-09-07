#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIRM_VALUE="DESTROY_HETZNER_STAGING"

if ! command -v terraform >/dev/null 2>&1; then
  echo "Terraform is required." >&2
  exit 1
fi

if [[ -z "${HCLOUD_TOKEN:-}" ]]; then
  echo "HCLOUD_TOKEN is required to destroy Hetzner staging resources." >&2
  exit 1
fi

if [[ "${INZOZI_CONFIRM_DESTROY:-}" != "${CONFIRM_VALUE}" ]]; then
  cat >&2 <<EOF
Refusing to destroy infrastructure without explicit confirmation.

When the staging review is complete, run with:
  INZOZI_CONFIRM_DESTROY=${CONFIRM_VALUE}

This destroys the disposable server, firewall, and Terraform-managed SSH key.
EOF
  exit 2
fi

terraform -chdir="${HERE}" init -input=false
terraform -chdir="${HERE}" plan -destroy -input=false -out=destroy.tfplan
terraform -chdir="${HERE}" apply -input=false destroy.tfplan
rm -f "${HERE}/destroy.tfplan" "${HERE}/staging.tfplan"

cat <<'EOF'
Hetzner staging resources destroyed.
Server compute billing should stop once Hetzner has completed deletion.
Keep code, application secrets, and production infrastructure separate from this disposable environment.
EOF
