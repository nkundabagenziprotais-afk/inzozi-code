# Workspace XFS project hard quotas

## Goal

Issue #20 requires a real per-workspace disk boundary before public staging. Measuring a Docker volume after a write is not a hard quota: one workspace can temporarily consume host capacity before cleanup runs.

This milestone moves workspace repository data onto a dedicated XFS filesystem mounted with project-quota enforcement and assigns every workspace directory a unique XFS project ID with a hard byte limit.

## Storage boundary

```text
Hetzner XFS volume
mounted /srv/inzozi-code/workspace-data with prjquota

  workspace-data/
    .inzozi-projects/        root-only project-ID registry
    <workspace-a>/           XFS project ID A, hard limit 2 GiB
    <workspace-b>/           XFS project ID B, hard limit 2 GiB
```

A Docker local volume remains as an alias for compatibility with the existing runtime/helper code, but its `device` is the broker-selected workspace directory and its driver options are fixed to a local bind mount. The API and workspace user never supply a host path.

## Fixed-purpose quota helper

Only the privileged workspace broker may launch the quota helper image. The helper:

- has no network;
- accepts only `check`, `setup`, `destroy`, and `probe` actions;
- receives only a strict 32-character workspace ID and broker-configured disk limit;
- mounts only the configured quota storage root;
- uses XFS project IDs allocated under a root-only registry outside workspace-visible directories;
- assigns the project ID directly with `xfs_io`, enables the XFS project-inherit flag, and verifies both before continuing;
- applies/clears the project hard limit through `setquota -P -F xfs`, avoiding false-positive `xfs_quota` command success on a container bind mount;
- uses only `SYS_ADMIN` and `CHOWN` inside the short-lived quota helper container;
- is removed after every operation.

The long-lived workspace manager remains unprivileged and has no Docker socket.

## Provisioning order

1. Validate the workspace ID, GitHub URL, ref, TTL, and active-workspace capacity.
2. Ask the fixed quota helper to allocate a unique project ID.
3. Assign that ID to the workspace root, enable project inheritance, and verify the inode reports the exact allocated project ID before any clone can begin.
4. Apply the hard XFS project byte limit through the quota syscall tooling.
5. Create a Docker local bind-volume alias pointing to the broker-derived workspace directory.
6. Create the workspace's private internal Docker network.
7. Run the short-lived GitHub bootstrap helper against the already-quoted storage.
8. Measure repository size as defense-in-depth.
9. Start the isolated long-lived runtime with CPU, memory, PID, tmpfs, capability, and egress restrictions.

If any step fails, the broker removes the runtime/network/volume alias and asks the quota helper to remove the XFS project directory and clear its limit.

## Enforcement evidence

The staging preflight requires all of the following:

- broker reports `disk_quota_mode=xfs-project-hard`;
- quota filesystem is XFS;
- project quota enforcement is active;
- every active workspace has a project ID;
- active project IDs are unique;
- helper setup fails closed unless `xfs_io lsproj` reports the exact allocated project ID and the directory has project inheritance enabled;
- a disposable quota probe sets a 1 MiB hard project limit and verifies that a 2 MiB write fails with `ENOSPC`, which is the XFS project-quota exhaustion errno;
- the probe directory and limit are removed afterward.

The previous post-write `du` check remains enabled as defense-in-depth and anomaly detection, but it is no longer the primary quota boundary.

## Host configuration

Terraform attaches a separate XFS Hetzner volume. The reviewed operator script mounts that exact device at `/srv/inzozi-code/workspace-data` using `prjquota` and refuses to format unexpected storage or rewrite an unexpected existing fstab entry.

The broker intentionally fails closed during startup if the quota helper image is missing or if the helper cannot verify XFS project quota enforcement.

## Residual public-staging blockers

This milestone addresses the hard disk-quota portion of issue #20. It does not by itself enable public staging. Remaining work includes:

- restrict helper egress beyond a generic internet-capable Docker bridge;
- move applicable rate/session controls to durable Redis-backed state;
- run cross-tenant/path traversal/symlink/command escape and resource-exhaustion test suites;
- complete HTTPS/public DNS review and set `AUTH_COOKIE_SECURE=true` only after the security milestone is accepted.
