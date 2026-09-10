# Issue #39 — backup, monitoring, and rollback readiness

Parent release gate: #35

This document defines the operational recovery contract for public staging work. It does **not** authorize public DNS, public ingress, production deployment, Terraform apply, provider billing changes, destructive restore, or automatic merge.

## Accepted private-staging baseline

The implementation branch starts from the accepted private-staging commit:

`a4765a16dbe0d55f80e8d6f3621448f68a187be2`

At entry to Issue #39:

- database identity mode is active;
- the bootstrap identity is absent;
- exactly one durable Platform Owner remains active;
- temporary acceptance identities are disabled;
- active workspace runtime/volume/network count is zero;
- `/health` and `/ready` are green;
- public DNS is disabled;
- production is unchanged;
- the live origin firewall contains only TCP 22/80/443 from the approved operator `/32`.

The repository Terraform definition still contains an ICMP rule while the accepted live firewall does not. Terraform apply remains blocked until this drift is reconciled in reviewed code.

## Live baseline findings

The read-only operational audit established:

- Hetzner server backups are disabled;
- server delete protection is disabled;
- server rebuild protection is disabled;
- the attached workspace XFS volume is not delete-protected;
- PostgreSQL `pg_dump` is available inside the PostgreSQL container;
- PostgreSQL WAL archiving is off;
- Redis AOF is enabled with `appendfsync=everysec`;
- Redis currently contains no keys;
- `/srv/inzozi-code/backups` exists with mode `0700` but is empty;
- no application-specific backup or monitoring systemd units exist;
- there are no application backup cron jobs;
- Prometheus, Alertmanager, Grafana, and Node Exporter are not active;
- Docker uses `json-file` logging without daemon-level rotation;
- Nginx logrotate is present and journald is persistent;
- root filesystem use is low and the XFS quota pool has substantial free capacity;
- the private staging certificate has a short remaining lifetime and therefore requires expiry monitoring.

The systemd names matched by the broad inventory were operating-system facilities only (`dpkg-db-backup.timer`, `lvm2-monitor.service`, `mdmonitor*.service`) and are not Inzozi Code backup/monitoring controls.

## Data durability classification

### PostgreSQL — authoritative and durable

PostgreSQL is the authoritative recovery source for:

- durable identities and roles;
- invitation history/state;
- workspace ownership metadata;
- workspace access audit records;
- other application relational state introduced by reviewed migrations.

PostgreSQL must be backed up off-host using an encrypted repository. A backup is not accepted until a restore is proven into an isolated clean recovery target.

**Target RPO:** no more than 6 hours.

**Target RTO:** no more than 60 minutes for restoration of the database and service readiness on a clean staging recovery target.

### Redis — recoverable session/control state

Redis is not an authoritative user or audit datastore. Local AOF remains useful for restart continuity, but disaster recovery is allowed to start Redis empty. Users may be required to authenticate again after a disaster recovery event.

Redis must continue to fail closed when unavailable, but off-host Redis backup is not required for Issue #39 acceptance.

### Workspace XFS data — disposable / TTL-bound

Workspace repository directories are intentionally short-lived execution state. They are protected by hard XFS project quotas and workspace TTL/lifecycle controls, but are **not** part of the disaster-recovery backup set.

The PostgreSQL ownership/audit metadata remains durable even after workspace runtime data is destroyed.

### Configuration and source

Reviewed application/infrastructure source is recovered from protected Git history at an exact reviewed SHA.

Server secrets are never committed to Git. Recovery requires separately controlled secret material, including the staging environment file, GitHub App key, auth/session secret, backup repository credentials, and any alert-delivery secret.

## Backup architecture

### Backup mechanism

Use a PostgreSQL logical backup with `pg_dump --format=custom` from the running PostgreSQL container. The reviewed backup script streams the custom dump directly into an **off-host Restic repository** using Restic stdin backup mode, so no plaintext database dump is written to local disk.

Restic is selected because it provides client-side authenticated encryption, repository integrity checking, retention policies, and restore without placing plaintext database dumps in an external object store.

The repository URL, Restic password file path, and backend credentials must exist only in a root-readable host secret file. They must never be committed, echoed, written to journals, or included in issue/PR output.

### Backup frequency

Run PostgreSQL backup every 6 hours.

The timer may use a small randomized delay to avoid a fixed public operational signature, but the worst-case interval must still meet the 6-hour RPO.

### Retention

Initial public-staging retention policy:

- retain all snapshots from the most recent 24 hours;
- retain 7 daily snapshots;
- retain 4 weekly snapshots;
- retain 3 monthly snapshots.

Retention must be executed by Restic `forget` using the reviewed policy. Pruning should be separated from the critical backup success path when practical so a retention failure cannot invalidate a successfully uploaded new backup.

### Plaintext handling

- the database dump is streamed from `pg_dump` to Restic and is not persisted as a local plaintext file;
- Restic repository encryption occurs client-side before off-host storage;
- root-only Restic credentials/password files must be mode `0400` or `0600`;
- backup filenames must contain no email address, token, credential, user identifier, or repository secret;
- interrupted runs must not leave a local plaintext database dump.

### Verification

The backup workflow records only sanitized operational markers such as timestamp, duration, Restic snapshot success, and retention/check result.

At least weekly, run `restic check`. Public-staging acceptance additionally requires a full restore test of a current snapshot.

## Restore acceptance

A restore test must not overwrite the accepted live PostgreSQL volume.

The reviewed restore-test workflow must:

1. fetch a selected Restic snapshot from off-host storage;
2. create an explicitly labelled diagnostic PostgreSQL 16 recovery container and dedicated diagnostic Docker volume;
3. run the diagnostic database with `--network none` and no published ports;
4. stream the Restic dump directly into `pg_restore` in the isolated recovery database;
5. verify schema presence and non-secret row-count invariants;
6. verify that the expected single active Platform Owner state is present at the selected backup point, without printing identity values;
7. verify workspace ownership/audit tables can be queried;
8. remove only the explicitly labelled diagnostic container/volume after evidence is captured;
9. leave the accepted live database, Redis, workspace pool, firewall, DNS, and source checkout unchanged.

No direct restore into the live database is part of acceptance.

## Monitoring contract

The staging operations monitor must run at least every 5 minutes and produce a non-secret success/failure result for:

- HTTPS `/health` availability;
- HTTPS `/ready` availability;
- PostgreSQL container health;
- Redis container health;
- workspace-manager health;
- workspace-broker health;
- workspace-egress-proxy health;
- host Nginx active/config-valid state;
- root filesystem pressure;
- XFS workspace-pool pressure;
- backup freshness;
- TLS certificate expiry;
- repeated HTTP 5xx response threshold;
- unexpected workspace-runtime residue where zero is required by the relevant operational state.

### Initial alert thresholds

- readiness failure: alert on first failed monitoring run and continue until recovery;
- health failure: alert on first failed monitoring run;
- PostgreSQL/Redis/control-plane health failure: alert immediately;
- latest successful PostgreSQL backup older than 7 hours: alert;
- root filesystem usage >= 80%: warning; >= 90%: critical;
- workspace XFS pool usage >= 80%: warning; >= 90%: critical;
- TLS expiry <= 14 days: warning; <= 7 days: critical;
- repeated 5xx: critical when the reviewed rolling-window threshold is exceeded.

Alert payloads must contain only sanitized service/status information. Do not include cookies, Authorization headers, request bodies, database URLs, tokens, passwords, password hashes, GitHub installation tokens, private keys, or environment dumps.

## Alert delivery and escalation

Alert delivery must use a separately configured operator endpoint/credential stored outside Git. If delivery is unavailable, the monitoring job must still fail and log a sanitized local alert marker to journald.

Escalation runbook:

1. operator confirms `/health` and `/ready` from the approved network;
2. inspect only the affected service status and bounded sanitized logs;
3. if identity DB or Redis is unavailable, preserve fail-closed behavior instead of bypassing authentication;
4. if disk pressure is critical, stop creation of new workspaces before considering cleanup;
5. if backup age is critical, create/verify a fresh backup before non-essential deployment;
6. if TLS expiry is critical, renew/replace the private/public certificate through the reviewed edge workflow;
7. if recovery cannot be completed within the stated RTO, execute the private-staging rollback procedure.

## Log retention

Docker `json-file` logs must be bounded before public staging. Configure size/file rotation without changing application log content or introducing secret logging.

Nginx logrotate and persistent journald may remain, but retention settings must be documented and capacity-monitored.

Security/audit logs must remain useful for incident investigation while preserving the secret-boundary acceptance from Issue #37.

## Private-staging rollback contract

**Target rollback RTO:** no more than 30 minutes to return the edge to the accepted PRIVATE STAGING boundary after a public-staging failure.

Rollback means:

- public DNS is disabled/removed as applicable;
- origin firewall is restricted back to the approved operator CIDR only;
- private TLS path is restored if public TLS configuration had been activated;
- application is deployed only from an exact reviewed commit with a clean checkout;
- database is not rolled backward destructively unless a separate data-recovery decision is explicitly made;
- `/health` and `/ready` return 200;
- database identity mode remains active;
- bootstrap identity remains absent;
- no temporary acceptance users or workspaces are introduced by rollback.

The rollback runbook must distinguish **application/edge rollback** from **database restore**. A code rollback must never silently restore an older database dump.

## Terraform drift and provider protection

The accepted live firewall has no ICMP rule. The Issue #39 branch removes the stale ICMP declaration from `infrastructure/hetzner/main.tf`; this is a repository-only reconciliation and does not authorize or perform Terraform apply.

Provider backups/delete/rebuild protection are currently disabled. Enabling provider backups may create recurring cost and therefore requires a separate explicit operator decision before apply. Issue #39 can be accepted using the reviewed encrypted off-host PostgreSQL backup mechanism even if provider image backups remain disabled, provided restore acceptance passes.

Delete/rebuild protection should be represented in reviewed Terraform before public staging, but applying those protection changes remains a separate controlled provider mutation.

## Implementation sequence

1. Reconcile the Terraform ICMP rule in code only.
2. Add Restic-backed PostgreSQL backup tooling.
3. Add isolated restore-test tooling.
4. Add systemd service/timer templates and a guarded installer.
5. Add bounded Docker log rotation configuration/installer.
6. Add monitoring script, alert adapter, and timer.
7. Add the private-staging rollback runbook/script.
8. Extend CI shell syntax/static checks for all new reviewed scripts.
9. Open a PR; do not auto-merge.
10. After CI/review approval, explicitly merge and deploy the reviewed commit.
11. Configure backup/alert secrets out-of-band on staging.
12. Prove backup, off-host retention, isolated restore, monitoring failure detection, and rollback.
13. Perform final Issue #39 acceptance and only then close the issue.

## Non-negotiable safety rules

- no public DNS activation during Issue #39;
- no firewall widening during Issue #39;
- no production deployment;
- no Terraform apply while the firewall definition is inconsistent with the accepted live state;
- no secrets committed to Git or printed to terminal/CI/issues;
- no live database overwrite during restore acceptance;
- no direct database manipulation to bypass application controls;
- no automatic merge;
- do not touch unrelated PRs or production infrastructure.
