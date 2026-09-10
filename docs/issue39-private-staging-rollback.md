# Issue #39 — private-staging rollback runbook

This runbook returns Inzozi Code to a previously accepted **PRIVATE STAGING** boundary. It is not a database-restore procedure and it does not authorize production changes.

## Preconditions

- identify one exact reviewed commit that was previously accepted as the private-staging rollback target;
- confirm the staging checkout has no local modifications;
- stop new workspace creation and disconnect active workspaces through normal application controls;
- confirm no workspace runtime/container residue remains;
- preserve PostgreSQL and Redis volumes; do not manually delete application Docker volumes;
- do not use `git reset --hard`;
- do not restore an older database dump unless a separate data-recovery decision is explicitly approved.

## External edge rollback first

The repository rollback script intentionally does **not** mutate DNS or the Hetzner firewall. Those controls must be changed through their normal provider control planes and independently verified.

1. Disable/remove public staging DNS records as applicable.
2. Restrict origin ingress back to the approved operator CIDR only.
3. Require the accepted private rule shape: TCP 22/80/443 only, no internet-wide source ranges, and no public ICMP rule.
4. Confirm the approved operator can still reach SSH/HTTP/HTTPS.
5. Do not run Terraform apply as a shortcut during an incident unless the reviewed Terraform state and live state have first been reconciled.

## Application/private-TLS rollback

Run the reviewed rollback script from the staging checkout as root only after the external edge has been confirmed private:

```bash
ROLLBACK_COMMIT_SHA='<exact-reviewed-private-staging-sha>' \
CONFIRM_PUBLIC_DNS_DISABLED=YES \
CONFIRM_FIREWALL_PRIVATE=YES \
CONFIRM_DATABASE_RESTORE_NOT_REQUESTED=YES \
  infrastructure/staging/rollback-private-staging.sh
```

The script:

- refuses a dirty checkout;
- requires the exact rollback commit to already exist locally;
- refuses active workspace runtime residue;
- switches to the exact reviewed commit without `git reset --hard`;
- deploys only through `deploy-staging.sh` with `EXPECTED_COMMIT_SHA`;
- restores the reviewed private HTTPS host-Nginx configuration;
- verifies database identity mode, secure cookies, ownership enforcement, and bootstrap-identity absence;
- verifies `/health` and `/ready` return 200;
- does not restore a database, modify Redis data, change DNS, mutate the firewall, or touch production.

## Verification after rollback

Require all of the following before declaring rollback complete:

- exact rollback SHA is checked out and the checkout is clean;
- public DNS is disabled/absent as appropriate;
- origin firewall is private and limited to the approved operator CIDR;
- private TLS is active and trusted only through the private staging CA workflow;
- database identity mode remains active;
- bootstrap credentials remain absent;
- exactly the intended durable identities remain active;
- no temporary acceptance actors have been re-enabled;
- active workspace runtime/volume/network residue is zero unless explicitly expected;
- `/health` is 200;
- `/ready` is 200;
- no database restore occurred as part of the code/edge rollback;
- production is unchanged.

## RTO measurement

The Issue #39 target is no more than **30 minutes** from rollback decision to the accepted private-staging application/edge boundary. Capture start/end timestamps and the script's sanitized `ROLLBACK_DURATION_SECONDS` marker during acceptance testing.

If application/edge rollback succeeds but data recovery is required, stop and invoke the separate PostgreSQL disaster-recovery procedure. Never silently pair a code rollback with an older database snapshot.
