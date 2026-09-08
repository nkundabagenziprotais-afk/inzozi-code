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
4. Set AUTH_COOKIE_SECURE=true in the mode-600 staging environment.
5. Export the exact reviewed 40-character commit as EXPECTED_COMMIT_SHA.
6. Deploy the exact reviewed checkout first:
   EXPECTED_COMMIT_SHA="${EXPECTED_COMMIT_SHA}" \
     /srv/inzozi-code/application/infrastructure/staging/deploy-staging.sh
7. Confirm the running API loaded the secure-cookie setting:
   REQUIRE_SECURE_COOKIE=true \
     /srv/inzozi-code/application/infrastructure/staging/auth-preflight.sh
8. Activate the private HTTP vhost with:
   sudo EXPECTED_COMMIT_SHA="${EXPECTED_COMMIT_SHA}" MODE=http \
     APP_ROOT=/srv/inzozi-code/application \
     /srv/inzozi-code/application/infrastructure/staging/configure-host-nginx.sh
9. Generate private staging TLS without public DNS:
   sudo /srv/inzozi-code/application/infrastructure/staging/prepare-private-tls.sh
10. Activate HTTPS with:
    sudo EXPECTED_COMMIT_SHA="${EXPECTED_COMMIT_SHA}" MODE=https \
      APP_ROOT=/srv/inzozi-code/application \
      /srv/inzozi-code/application/infrastructure/staging/configure-host-nginx.sh
11. Run the final HTTPS preflight only after deployment:
    EXPECTED_COMMIT_SHA="${EXPECTED_COMMIT_SHA}" \
      /srv/inzozi-code/application/infrastructure/staging/https-preflight.sh
12. Keep public DNS disabled.
EOF

docker --version
docker compose version
nginx -t
