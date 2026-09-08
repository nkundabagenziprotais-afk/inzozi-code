# Hetzner ephemeral staging

This directory provisions a **disposable** Inzozi Code staging host on Hetzner Cloud. It is intentionally separate from production and from the application deployment logic in `infrastructure/staging/`.

## Default shape

- provider: Hetzner Cloud
- server type: `cx33`
- location: `nbg1` (Nuremberg, Germany)
- image: Ubuntu 24.04
- lifecycle: ephemeral
- backups: disabled
- delete protection: disabled
- workspace quota pool: 30 GB XFS volume, project quotas enabled during host configuration
- HTTP and HTTPS use separate explicitly supplied CIDRs; neither has an internet-wide default
- SSH: **must** be restricted to explicit operator CIDRs

Always check Hetzner's current server, IPv4, and volume pricing before creating or expanding the host. Pricing is deliberately not hard-coded into the Terraform configuration.

## Trust boundaries

Terraform manages only the temporary cloud host, firewall, staging SSH public-key registration, and the disposable XFS workspace-quota volume.

It does **not** manage:

- `OPENAI_API_KEY`
- GitHub App private keys
- application `.env` values
- production DNS
- cPanel
- production deployment
- SSH private keys

The Hetzner API token is read from `HCLOUD_TOKEN` in the local process environment. Never put it in a `.tf`, `.tfvars`, `.env`, issue, pull request, or chat message.

## Prerequisites

1. Create a dedicated Hetzner Cloud project for Inzozi Code staging.
2. Create a project API token with the access required to create/delete the staging resources.
3. Install Terraform 1.16.x.
4. Have `ssh-keygen`, `curl`, and Bash available locally.
5. Restrict SSH to your current public IPv4 as a `/32` whenever possible.

## Prepare the local operator machine

Run the helper from the repository root:

```bash
./infrastructure/hetzner/prepare-local.sh
```

It will:

- create a dedicated Ed25519 staging key if one does not already exist;
- preserve and reuse an existing complete key pair rather than overwrite it;
- recommend a passphrase for the operator key;
- detect the current public IPv4 for the SSH `/32` firewall rule;
- print the exact non-secret Terraform environment setup;
- never read, store, or print the Hetzner API token.

The helper does not create any cloud resources and therefore starts no Hetzner compute billing.

## Environment

After local preparation, set the project API token only in your own terminal or secret manager, then export the non-secret Terraform variables shown by the helper. Equivalent example values are:

```bash
export HCLOUD_TOKEN='set-this-securely-outside-git'
export SSH_KEY_PATH="$HOME/.ssh/inzozi_code_staging_ed25519"
export TF_VAR_ssh_public_key="$(cat "$SSH_KEY_PATH.pub")"
export TF_VAR_ssh_source_cidrs='["203.0.113.10/32"]'
export TF_VAR_http_source_cidrs='["203.0.113.10/32"]'
export TF_VAR_https_source_cidrs='["203.0.113.10/32"]'
```

Do not paste the real token or private key into project files or chat.

`http_source_cidrs` and `https_source_cidrs` have no defaults.
For private staging, normally keep both restricted to the operator's current
`/32`, just like SSH. This HTTPS hardening phase does not enable public DNS
or internet-wide ingress.

## Create

Creation is deliberately two-step and refuses to proceed without the explicit billing confirmation value:

```bash
INZOZI_CONFIRM_CREATE=CREATE_HETZNER_STAGING \
  ./infrastructure/hetzner/up.sh
```

The script runs `terraform init`, formatting checks, validation, an execution plan, then applies that exact plan. The default plan includes the disposable XFS workspace quota volume. `workspace_quota_volume_size_gb` can be changed deliberately before planning; it is not the same as the per-workspace byte limit.

## Bootstrap and verify

After creation:

```bash
./infrastructure/hetzner/bootstrap.sh
```

If your staging SSH key is not the default key selected by SSH:

```bash
SSH_KEY_PATH=~/.ssh/inzozi_code_staging_ed25519 \
  ./infrastructure/hetzner/bootstrap.sh
```

The wrapper waits for cloud-init, copies the provider-neutral `infrastructure/staging/bootstrap-host.sh`, runs it with `sudo`, then verifies Docker, Docker Compose, and Nginx.

## Configure hard workspace quota storage

Before deploying a broker revision that requires hard disk quotas, disconnect all active workspaces and run:

```bash
export SSH_KEY_PATH="$HOME/.ssh/inzozi_code_staging_ed25519"
./infrastructure/hetzner/configure-workspace-quota-storage.sh
```

This script reads the reviewed Terraform outputs, then on the staging host it:

- refuses to proceed if a workspace runtime is active;
- refuses to format an unexpected device;
- verifies the Terraform-created volume is already XFS;
- mounts it at `/srv/inzozi-code/workspace-data` through `/etc/fstab` with `prjquota`;
- verifies the mounted UUID, filesystem type, and mount options;
- runs a destructive **probe only inside a temporary quota test directory**: a 2 MiB write must be rejected by a 1 MiB XFS project hard limit;
- cleans the probe and leaves the quota pool mounted for broker-managed workspace directories.

The script does not read application credentials and does not expose the Hetzner API token.

## Application deployment

After the host and quota storage are verified:

1. Put the **reviewed** Git checkout at `/srv/inzozi-code/application`.
2. Create `/srv/inzozi-code/.env.staging` on the server with mode `600`.
3. Mount the GitHub App PEM into the API container through the staging Compose override; never commit it.
4. Keep both HTTP and HTTPS firewall CIDRs restricted to the approved operator network.
5. Activate the reviewed private HTTP vhost with `sudo MODE=http infrastructure/staging/configure-host-nginx.sh`.
6. Generate the short-lived private staging TLS identity with `sudo infrastructure/staging/prepare-private-tls.sh`; this requires no public DNS.
7. Set `AUTH_COOKIE_SECURE=true` in the mode-600 staging environment, recreate the API, and run `REQUIRE_SECURE_COOKIE=true infrastructure/staging/auth-preflight.sh`.
8. Activate private HTTPS with `sudo MODE=https infrastructure/staging/configure-host-nginx.sh`.
9. Run `infrastructure/staging/https-preflight.sh`.
10. Run `infrastructure/staging/deploy-staging.sh`.
11. Run `infrastructure/staging/dedicated-workspace-preflight.sh`.
12. Keep public DNS disabled; a publicly trusted certificate and public ingress remain a separate future release gate.

## Destroy when review is complete

Stopping or powering off a cloud server is **not** our cost-control mechanism. The disposable workflow ends with destruction:

```bash
INZOZI_CONFIRM_DESTROY=DESTROY_HETZNER_STAGING \
  ./infrastructure/hetzner/down.sh
```

The destroy script plans the deletion first and then applies that exact destroy plan. The Terraform-managed XFS quota volume is part of the disposable stack and is destroyed with the staging infrastructure. Confirm in the Hetzner console that both the server and workspace quota volume are gone after completion.

## Recreate later

The intended rhythm is:

```text
GitHub + CI
  -> prepare local operator machine
  -> create temporary Hetzner staging + XFS quota volume
  -> bootstrap
  -> configure XFS project quota storage
  -> deploy reviewed commit
  -> browser / Aquila / Git workflow testing
  -> capture findings
  -> destroy staging
  -> continue development in GitHub
```

This keeps development infrastructure inexpensive without coupling Inzozi Code to Hetzner permanently.
