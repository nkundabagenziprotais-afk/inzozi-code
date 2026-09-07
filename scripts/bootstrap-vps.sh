#!/usr/bin/env bash
set -euo pipefail

if [ "$(id -u)" -eq 0 ]; then
  echo "Run this script as the non-root deployment user with sudo access." >&2
  exit 1
fi

sudo apt-get update
sudo apt-get install -y ca-certificates curl git ufw

sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw --force enable

cat <<'MSG'
Base packages and firewall are ready.

Install Docker Engine and the Docker Compose plugin from Docker's current official Ubuntu instructions before deploying the stack. This bootstrap intentionally does not curl-pipe a remote installer as root.
MSG
