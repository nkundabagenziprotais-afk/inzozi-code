# Aquila Studio Private Staging — ai.inzozidigital.com

`ai.inzozidigital.com` is the canonical Aquila Studio application hostname.

## Recommended host

- Ubuntu 24.04 LTS
- 8 vCPU
- 32 GB RAM
- 250–500 GB NVMe
- Daily snapshot/backups

## Current private-staging boundary

Private staging keeps the Hetzner origin constrained to the reviewed operator boundary. The canonical hostname is used by host Nginx, private TLS, authentication cookies, and UI acceptance, but adopting the hostname does **not** by itself authorize public DNS or internet-wide ingress.

For private UI review, the authorized operator may resolve `ai.inzozidigital.com` to the staging origin locally while using the reviewed private CA certificate. Unknown hostnames must continue to be rejected by host Nginx.

## Public DNS and trusted TLS

Public activation is a separate release gate under Issue #38. When that gate is approved, create the reviewed DNS record for:

- Host/name: `ai`
- Zone: `inzozidigital.com`
- Target: the reviewed public edge/proxy, **not an unrestricted direct-origin exposure**

Do not change the existing `inzozidigital.com` website records. Do not widen the origin firewall merely to make the hostname publicly reachable.

## Server baseline

1. Create a non-root deploy user.
2. Disable password SSH and root SSH login after key access is verified.
3. Keep SSH restricted to the reviewed operator source.
4. Install Docker Engine and the Docker Compose plugin from the official Docker repository.
5. Clone the GitHub repository into `/srv/inzozi-code/application`.
6. Create the root-private staging environment file from `.env.example` with strong secrets.
7. Deploy only an exact reviewed Git commit using the staging deployment script.
8. Configure host Nginx for `ai.inzozidigital.com` using the reviewed private-staging templates.
9. Generate/verify private staging TLS for `ai.inzozidigital.com` until the separate public-edge/trusted-TLS gate is approved.
10. Verify the canonical hostname, unknown-host rejection, Secure cookies, `/health`, `/ready`, and Product Control UI acceptance.

## Deployment rule

Do not edit application source files manually on the server. Deploy only reviewed Git commits. Merge, private-staging deployment, public DNS, public ingress, trusted public TLS, Terraform apply, and production remain separate explicit gates.
