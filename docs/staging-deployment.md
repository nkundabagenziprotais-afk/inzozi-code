# Staging Deployment — code-staging.inzozidigital.com

## Recommended host

- Ubuntu 24.04 LTS
- 8 vCPU
- 32 GB RAM
- 250–500 GB NVMe
- Daily snapshot/backups

## DNS

Create an A record:

- Host/name: `code-staging`
- Value: `<STAGING_VPS_PUBLIC_IP>`
- TTL: automatic/default

Do not change the existing `inzozidigital.com` website records.

## Server baseline

1. Create a non-root deploy user.
2. Disable password SSH and root SSH login after key access is verified.
3. Enable firewall; expose 22 (restricted where practical), 80 and 443.
4. Install Docker Engine and Docker Compose plugin from the official Docker repository.
5. Clone the GitHub repository into `/srv/inzozi-code/application`.
6. Create a production-safe `.env` from `.env.example` with a strong database password.
7. Run `docker compose up -d --build`.
8. Put a host Nginx/Caddy/Traefik TLS proxy in front of the stack or adapt the included proxy for HTTPS.
9. Obtain TLS for `code-staging.inzozidigital.com`.
10. Verify `https://code-staging.inzozidigital.com/health`.

## Deployment rule

Do not edit source files manually on the server. Deploy only reviewed Git commits.
