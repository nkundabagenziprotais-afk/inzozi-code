# Inzozi Code v0.1.18 — Brokered workspace control plane

## Goal

Remove raw Docker daemon access from the long-lived `workspace-manager` without weakening the dedicated per-workspace execution model proven on staging in PR #22.

This hardening milestone is intentionally separate from the staging-proven execution-isolation branch.

## Trust boundary

The control plane is split into two services:

- `workspace-manager`: unprivileged UID 10003, no Docker socket, receives API requests and proxies guarded workspace operations;
- `workspace-broker`: the only service with `/var/run/docker.sock`, reachable only on the internal `workspace-control` network, and exposes a fixed lifecycle API rather than the Docker API.

The API service is not attached to `workspace-control`. Dedicated workspace runtimes are not attached to it either.

Manager-to-broker calls require a server-side `WORKSPACE_BROKER_TOKEN` and the broker also validates that the source address resolves to the manager service. Staging requires a non-empty broker token through Compose; the tracked development fallback must never be used for staging or production.

## Broker operation allowlist

The broker exposes only:

- create a workspace from a validated GitHub HTTPS repository;
- destroy one validated workspace ID;
- enforce the configured workspace disk threshold;
- execute an approval-gated non-force push for a safe branch and exact reviewed commit;
- return safe isolation status.

Helper execution is hardcoded to `app.bootstrap` and `app.remote_push`. The broker does not accept arbitrary image names, arbitrary commands, host bind mounts, privileged flags, device requests, host networking, host PID namespace, or arbitrary Docker API paths from callers.

## Dedicated runtime controls retained

Each workspace still receives:

- one dedicated container;
- one dedicated named volume;
- one dedicated `internal=true` network;
- read-only root filesystem;
- all Linux capabilities dropped;
- `no-new-privileges`;
- CPU, memory and PID ceilings;
- private `/tmp` tmpfs;
- TTL cleanup;
- active-workspace capacity ceiling;
- long-lived runtime egress denied;
- short-lived GitHub helper token only for the requested helper action.

## Staging secret setup

Before deploying this branch to staging, run:

```bash
bash infrastructure/hetzner/configure-workspace-broker.sh
```

The helper generates or preserves a high-entropy broker token directly on the staging host, keeps `/srv/inzozi-code/.env.staging` at mode `0600`, and never prints the secret.

## Preflight acceptance

After deployment, `infrastructure/staging/dedicated-workspace-preflight.sh` verifies:

- manager has no Docker socket and runs non-root;
- broker alone has the Docker socket;
- broker control network is internal;
- API cannot directly resolve the broker;
- brokered isolation status reports zero runtime socket mounts;
- runtime internet egress remains denied;
- GitHub helper egress still works;
- legacy shared runtime remains absent.

The existing ownership/auth/GitHub App preflights must also remain green, followed by a private repository connect/read/disconnect/reconnect smoke test.

## Residual blockers after this branch

This branch removes the raw socket from the manager, but does not by itself approve public staging.

Material remaining risks:

1. Workspace disk enforcement history is now XFS project hard quotas on the dedicated path; keep verifying ENOSPC semantics in staging preflight.
2. Restricted GitHub helper egress via connect-proxy is complete for the dedicated helper path.
3. Durable Redis-backed login throttling and server-side session registry/revocation are required for Issue #20 and land in the Redis security slice.
4. Escape/exhaustion testing remains required.
5. HTTPS and `AUTH_COOKIE_SECURE=true` hardening remain required before any public staging.

## Still disabled

- public staging DNS;
- `AUTH_COOKIE_SECURE=true` until staging is HTTPS-only;
- OpenAI credential activation;
- merge/auto-merge;
- production deployment.
