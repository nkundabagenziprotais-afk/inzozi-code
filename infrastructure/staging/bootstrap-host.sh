#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this bootstrap as root on the staging VPS." >&2
  exit 1
fi

if [[ ! -f /etc/os-release ]]; then
  echo "Unable to identify the operating system." >&2
  exit 1
fi

. /etc/os-release
if [[ "${ID:-}" != "ubuntu" ]]; then
  echo "This bootstrap is intentionally limited to Ubuntu hosts." >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y \
  ca-certificates \
  curl \
  git \
  nginx \
  openssl \
  certbot \
  python3-certbot-nginx \
  docker.io \
  docker-compose-v2

systemctl enable --now docker
systemctl enable --now nginx

install -d -m 0750 /srv/inzozi-code
install -d -m 0700 /srv/inzozi-code/secrets
install -d -m 0700 /srv/inzozi-code/backups
install -d -m 0750 /srv/inzozi-code/application

cat <<'EOF'

Inzozi Code staging host bootstrap completed.

Security note:
- This script intentionally did NOT change UFW/firewall rules.
- It did NOT configure DNS.
- It did NOT request an SSL certificate.
- It did NOT write application/API/GitHub secrets.

Next:
1. Put the reviewed Git checkout in /srv/inzozi-code/application.
2. Create /srv/inzozi-code/.env.staging with chmod 600.
3. Keep HTTP and HTTPS firewall CIDRs restricted to the approved operator network.
4. Activate the private HTTP vhost with:
   sudo MODE=http APP_ROOT=/srv/inzozi-code/application \
     /srv/inzozi-code/application/infrastructure/staging/configure-host-nginx.sh
5. Generate private staging TLS without public DNS:
   sudo /srv/inzozi-code/application/infrastructure/staging/prepare-private-tls.sh
6. Set AUTH_COOKIE_SECURE=true and recreate the API.
7. Activate HTTPS with MODE=https, then run infrastructure/staging/https-preflight.sh.
8. Keep public DNS disabled.
9. Run infrastructure/staging/deploy-staging.sh.
EOF

docker --version
docker compose version
nginx -t
