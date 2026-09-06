#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SSH_KEY_PATH="${SSH_KEY_PATH:-$HOME/.ssh/inzozi_code_staging_ed25519}"
ADMIN_USER="${ADMIN_USER:-inzozi}"
MOUNT_POINT="${WORKSPACE_QUOTA_STORAGE_ROOT:-/srv/inzozi-code/workspace-data}"

if [[ ! -f "${SSH_KEY_PATH}" ]]; then
  echo "SSH key not found: ${SSH_KEY_PATH}" >&2
  exit 1
fi

HOST="$(terraform -chdir="${SCRIPT_DIR}" output -raw ipv4_address)"
DEVICE="$(terraform -chdir="${SCRIPT_DIR}" output -raw workspace_quota_volume_device)"

if [[ -z "${HOST}" || -z "${DEVICE}" ]]; then
  echo "Terraform workspace quota volume outputs are unavailable. Apply the reviewed Terraform plan first." >&2
  exit 1
fi

echo "Configuring XFS project-quota workspace storage on ${HOST}."
echo "Device: ${DEVICE}"
echo "Mount: ${MOUNT_POINT}"

ssh -t -i "${SSH_KEY_PATH}" "${ADMIN_USER}@${HOST}" bash -s -- "${DEVICE}" "${MOUNT_POINT}" <<'REMOTE'
set -euo pipefail

DEVICE="$1"
MOUNT_POINT="$2"
PROBE_ID=2147483000
PROBE_DIR="${MOUNT_POINT}/.inzozi-quota-probe"

if docker ps --format '{{.Names}}' | grep -q '^inzozi-ws-'; then
  echo "Refusing quota-storage reconfiguration while a workspace runtime is active. Disconnect active workspaces first." >&2
  exit 1
fi

sudo apt-get update -qq
sudo apt-get install -y -qq xfsprogs >/dev/null

if [[ ! -b "${DEVICE}" ]]; then
  echo "Workspace quota device is not available as a block device: ${DEVICE}" >&2
  exit 1
fi

FSTYPE="$(lsblk -n -o FSTYPE "${DEVICE}" | head -n1 | xargs)"
if [[ "${FSTYPE}" != "xfs" ]]; then
  echo "Refusing to format or reuse unexpected storage. Expected XFS on ${DEVICE}, found '${FSTYPE:-unknown}'." >&2
  exit 1
fi

UUID="$(sudo blkid -s UUID -o value "${DEVICE}")"
if [[ -z "${UUID}" ]]; then
  echo "Unable to read the XFS volume UUID." >&2
  exit 1
fi

sudo mkdir -p "${MOUNT_POINT}"

EXISTING_LINE="$(awk -v mount_point="${MOUNT_POINT}" '$2 == mount_point {print; exit}' /etc/fstab || true)"
EXPECTED_SOURCE="UUID=${UUID}"
if [[ -n "${EXISTING_LINE}" ]]; then
  EXISTING_SOURCE="$(awk '{print $1}' <<<"${EXISTING_LINE}")"
  EXISTING_TYPE="$(awk '{print $3}' <<<"${EXISTING_LINE}")"
  EXISTING_OPTIONS="$(awk '{print $4}' <<<"${EXISTING_LINE}")"
  if [[ "${EXISTING_SOURCE}" != "${EXPECTED_SOURCE}" || "${EXISTING_TYPE}" != "xfs" || ",${EXISTING_OPTIONS}," != *",prjquota,"* ]]; then
    echo "Existing fstab entry for ${MOUNT_POINT} does not match the reviewed XFS prjquota configuration." >&2
    echo "Refusing to rewrite it automatically: ${EXISTING_LINE}" >&2
    exit 1
  fi
else
  printf '%s %s xfs defaults,nofail,prjquota 0 2\n' "${EXPECTED_SOURCE}" "${MOUNT_POINT}" | sudo tee -a /etc/fstab >/dev/null
fi

if mountpoint -q "${MOUNT_POINT}"; then
  CURRENT_SOURCE="$(findmnt -n -o SOURCE --target "${MOUNT_POINT}")"
  CURRENT_UUID="$(findmnt -n -o UUID --target "${MOUNT_POINT}" || true)"
  CURRENT_TYPE="$(findmnt -n -o FSTYPE --target "${MOUNT_POINT}")"
  CURRENT_OPTIONS="$(findmnt -n -o OPTIONS --target "${MOUNT_POINT}")"
  if [[ "${CURRENT_UUID}" != "${UUID}" || "${CURRENT_TYPE}" != "xfs" || ",${CURRENT_OPTIONS}," != *",prjquota,"* ]]; then
    echo "${MOUNT_POINT} is already mounted with an unexpected source or without prjquota. Refusing to remount over live storage." >&2
    echo "source=${CURRENT_SOURCE} uuid=${CURRENT_UUID} type=${CURRENT_TYPE} options=${CURRENT_OPTIONS}" >&2
    exit 1
  fi
else
  sudo mount "${MOUNT_POINT}"
fi

sudo chown root:root "${MOUNT_POINT}"
sudo chmod 0750 "${MOUNT_POINT}"

MOUNT_TYPE="$(findmnt -n -o FSTYPE --target "${MOUNT_POINT}")"
MOUNT_OPTIONS="$(findmnt -n -o OPTIONS --target "${MOUNT_POINT}")"
if [[ "${MOUNT_TYPE}" != "xfs" || ",${MOUNT_OPTIONS}," != *",prjquota,"* ]]; then
  echo "XFS project quota mount verification failed." >&2
  exit 1
fi

cleanup_probe() {
  sudo rm -rf "${PROBE_DIR}" >/dev/null 2>&1 || true
  sudo xfs_quota -x -c "limit -p bsoft=0 bhard=0 ${PROBE_ID}" "${MOUNT_POINT}" >/dev/null 2>&1 || true
  sudo chmod 0750 "${MOUNT_POINT}" >/dev/null 2>&1 || true
}
trap cleanup_probe EXIT

sudo rm -rf "${PROBE_DIR}"
sudo mkdir "${PROBE_DIR}"
sudo xfs_quota -x -c "project -s -p ${PROBE_DIR} ${PROBE_ID}" "${MOUNT_POINT}" >/dev/null
sudo xfs_quota -x -c "limit -p bsoft=1m bhard=1m ${PROBE_ID}" "${MOUNT_POINT}" >/dev/null
sudo chown nobody:nogroup "${PROBE_DIR}"
sudo chmod 0751 "${MOUNT_POINT}"

set +e
sudo -u nobody dd if=/dev/zero of="${PROBE_DIR}/probe.bin" bs=1M count=2 conv=fsync status=none 2>/dev/null
PROBE_RC=$?
set -e

if [[ "${PROBE_RC}" -eq 0 ]]; then
  echo "Hard XFS project quota probe failed: a 2 MiB write exceeded a 1 MiB limit." >&2
  exit 1
fi

cleanup_probe
trap - EXIT

sudo xfs_quota -x -c state "${MOUNT_POINT}" | sed -n '1,14p'
echo "Workspace quota storage ready: XFS + prjquota + hard-limit probe passed."
REMOTE
