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
3. Configure host Nginx from infrastructure/staging/host-nginx.conf.template.
4. Point code-staging.inzozidigital.com to this host.
5. Request TLS only after DNS resolves to this server.
6. Run infrastructure/staging/deploy-staging.sh.
EOF

docker --version
docker compose version
nginx -t
