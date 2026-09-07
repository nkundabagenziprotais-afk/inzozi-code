# Inzozi Code — GitHub App staging E2E

This runbook activates private-repository access on the existing private Hetzner staging host while Aquila remains in mock mode.

The objective is to verify the full guarded Git flow:

`private clone → safe branch → edit → local commit approval → remote push approval → draft PR approval → stop`

Merge, auto-merge, production deployment, and public staging remain disabled.

## 1. GitHub App registration

Create the GitHub App under the GitHub account that owns the staging test repository.

Suggested settings:

- **Name:** `Inzozi Code Staging` (or another unique staging-only name)
- **Homepage URL:** an Inzozi Digital URL you control
- **User authorization/OAuth:** not required for this staging phase
- **Setup URL:** not required
- **Webhooks:** inactive; this phase does not consume webhook events
- **Repository permissions — Contents:** **Read & write**
- **Repository permissions — Pull requests:** **Read & write**
- **All other repository/organization/account permissions:** **No access** unless GitHub grants metadata read implicitly
- **Where can this GitHub App be installed?:** **Only on this account**

Why the App itself needs write permissions: Inzozi Code downscopes each short-lived installation token at runtime. Clone gets `contents:read`; remote push gets `contents:write`; draft PR creation gets `contents:read` plus `pull_requests:write`. A token cannot request a permission the installed App was not granted.

Do not grant Administration permission. Do not enable merge or deployment permissions.

## 2. Install only on the staging test repository

Install the App on the owner account and choose **Only select repositories**.

For the first E2E, select only:

`nkundabagenziprotais-afk/inzozi-code`

Do not grant access to all repositories.

Record locally:

- the numeric **App ID** (not the Client ID);
- the numeric **installation ID**;
- the private key PEM downloaded from the GitHub App settings page.

The PEM is a secret. Never paste it into chat, Git, `.env`, logs, tickets, or screenshots.

## 3. Generate a private key

From the GitHub App settings page, generate one private key and download the PEM to the Mac.

Keep the downloaded PEM local until the secure staging upload step. GitHub stores only the public portion of the generated key, so retain the PEM securely until it has been installed on staging and backed up in an appropriate secret store.

## 4. Configure the existing Hetzner staging host

From the local Inzozi Code checkout on the Mac:

```bash
cd ~/inzozi-code
export SSH_KEY_PATH="$HOME/.ssh/inzozi_code_staging_ed25519"

export GITHUB_APP_ID='YOUR_NUMERIC_APP_ID'
export GITHUB_APP_INSTALLATION_ID='YOUR_NUMERIC_INSTALLATION_ID'
export GITHUB_APP_PRIVATE_KEY_FILE="$HOME/Downloads/YOUR_GITHUB_APP_PRIVATE_KEY.pem"

./infrastructure/hetzner/configure-github-app.sh
```

Enter the actual values only in the local terminal. Do not send the PEM or its contents through chat.

The helper:

- reads the Hetzner staging IP from Terraform output;
- uploads the PEM over SSH to a temporary remote path;
- installs it outside the Git checkout at `/srv/inzozi-code/secrets/github-app.pem`;
- makes the key readable only by UID `10001`, the non-root API container user, with mode `0400`;
- removes the temporary upload;
- updates only the non-secret GitHub App identifiers and host key path in the untracked `/srv/inzozi-code/.env.staging`;
- keeps `.env.staging` mode `0600`;
- never prints private-key material.

## 5. Deploy the reviewed GitHub App E2E branch

Sync and deploy the exact reviewed branch to the private host using the normal staging workflow. Keep Aquila configured as:

```text
AQUILA_PROVIDER=mock
AQUILA_MODEL=mock-v0
```

Do not add `OPENAI_API_KEY` yet.

## 6. Run the GitHub App preflight

On the staging host, after the new API build is running:

```bash
cd /srv/inzozi-code/application
REPOSITORY_URL='https://github.com/nkundabagenziprotais-afk/inzozi-code' \
  bash infrastructure/staging/github-app-preflight.sh
```

The preflight verifies, without returning an installation token:

- the untracked staging env remains mode `0600`;
- the host PEM is mode `0400` and owned by UID `10001`;
- the non-root API container can read the mounted PEM;
- the GitHub App JWT is accepted by GitHub;
- the installation belongs to the configured owner;
- the installation is not suspended;
- Contents is Read & write;
- Pull requests is Read & write;
- a repository-scoped `contents:read` installation token can access exactly the selected private repository.

A preflight response must never contain the JWT, installation access token, or PEM content.

## 7. Private-repository E2E in the UI

Keep the SSH tunnel active and open `http://127.0.0.1:8080`.

Connect:

```text
https://github.com/nkundabagenziprotais-afk/inzozi-code.git
```

Use the default branch for the initial clone. Then:

1. Open **Git Review** and create the safe local branch `feature/github-app-e2e-smoke` before committing anything.
2. Open `README.md` and add one harmless temporary line such as `<!-- Inzozi Code GitHub App staging E2E -->`.
3. Save and confirm Git Status/Git Diff show only that intended change.
4. Prepare the local commit with message `test: verify GitHub App staging flow`.
5. Review the exact diff/tree and explicitly approve the local commit.
6. Confirm the UI still reports that nothing was pushed automatically.
7. Prepare remote push and review the exact branch/HEAD lock.
8. Explicitly approve remote push. No force push is allowed.
9. Prepare a draft PR targeting `main`.
10. Review the exact head/base/SHA/title/body lock and explicitly approve draft PR creation.
11. Confirm the GitHub PR is **draft**, not merged, and no deployment started.
12. Stop. Do not merge the smoke PR.

## 8. Cleanup after the smoke test

After evidence is captured:

- close the draft smoke PR in GitHub;
- delete the remote `feature/github-app-e2e-smoke` branch if it is no longer needed;
- disconnect/destroy the Inzozi Code workspace;
- keep the staging GitHub App installation only if the next private-repository tests are continuing;
- otherwise remove the App installation and revoke/delete the staging private key.

Do not destroy the Hetzner host until the current staging review cycle is complete. When the cycle is complete, use the existing explicit Hetzner destroy confirmation workflow.

## Security boundary

The GitHub App private key is the long-lived credential and must remain server-side/outside Git. Installation access tokens are short-lived, repository-scoped, downscoped to the action being approved, and must never be returned to the browser or logs.

This phase does not add authentication/RBAC, stronger per-workspace OS isolation, public DNS, merge, auto-merge, or production deployment. Those remain blockers before broader staging access.
