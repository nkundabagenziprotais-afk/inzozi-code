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
- HTTP/HTTPS: public
- SSH: **must** be restricted to explicit operator CIDRs

Always check Hetzner's current server and IPv4 pricing before creating the host. Pricing is deliberately not hard-coded into the Terraform configuration.

## Trust boundaries

Terraform manages only the temporary cloud host, firewall, and staging SSH public-key registration.

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
```

Do not paste the real token or private key into project files or chat.

## Create

Creation is deliberately two-step and refuses to proceed without the explicit billing confirmation value:

```bash
INZOZI_CONFIRM_CREATE=CREATE_HETZNER_STAGING \
  ./infrastructure/hetzner/up.sh
```

The script runs `terraform init`, formatting checks, validation, an execution plan, then applies that exact plan.

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

## Application deployment

After the host is verified:

1. Put the **reviewed** Git checkout at `/srv/inzozi-code/application`.
2. Create `/srv/inzozi-code/.env.staging` on the server with mode `600`.
3. Mount the GitHub App PEM into the API container through the staging Compose override; never commit it.
4. Configure host Nginx from `infrastructure/staging/host-nginx.conf.template`.
5. Point `code-staging.inzozidigital.com` to the temporary IPv4 only when you are ready for browser testing.
6. Request TLS only after DNS resolves correctly.
7. Run `infrastructure/staging/deploy-staging.sh` on the server.
8. Verify `/health`, logs, Git commit SHA, and UI at 360, 430, 768, 1280, 1440, and 1920 px.

## Destroy when review is complete

Stopping or powering off a cloud server is **not** our cost-control mechanism. The disposable workflow ends with destruction:

```bash
INZOZI_CONFIRM_DESTROY=DESTROY_HETZNER_STAGING \
  ./infrastructure/hetzner/down.sh
```

The destroy script plans the deletion first and then applies that exact destroy plan. Confirm in the Hetzner console that the server is gone after completion.

## Recreate later

The intended rhythm is:

```text
GitHub + CI
  -> prepare local operator machine
  -> create temporary Hetzner staging
  -> bootstrap
  -> deploy reviewed commit
  -> browser / Aquila / Git workflow testing
  -> capture findings
  -> destroy staging
  -> continue development in GitHub
```

This keeps development infrastructure inexpensive without coupling Inzozi Code to Hetzner permanently.
