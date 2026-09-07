# Workspace Manager

The workspace runtime is the first executable-code security boundary between Aquila and a project repository.

## V0.1.1 implemented contract

- `POST /v1/workspaces` — clone a GitHub HTTPS repository into a generated workspace.
- `GET /v1/workspaces/{id}/tree` — browse files without exposing `.git`.
- `GET /v1/workspaces/{id}/files/{path}` — read UTF-8 files with size limits.
- `PUT /v1/workspaces/{id}/files/{path}` — guarded writes with optional SHA-256 optimistic locking.
- `POST /v1/workspaces/{id}/actions` — execute predefined safe recipes only.
- `GET /v1/workspaces/{id}/git/status` — real Git status.
- `GET /v1/workspaces/{id}/git/diff` — real Git diff.
- `DELETE /v1/workspaces/{id}` — remove the workspace.

## Safety boundaries

- No `/bin/sh -c` or arbitrary shell command API.
- No Docker host socket mounted into the workspace service.
- Workspace paths are resolved and rejected if they escape `/workspaces/<id>/repo`.
- `.git` metadata cannot be edited through the file API.
- File reads/writes and command output have size limits.
- Commands are recipe-based and time-limited.
- Clone prompts are disabled (`GIT_TERMINAL_PROMPT=0`) so credentials are never requested interactively.
- Every mutation and command execution writes an audit event.
- Production credentials are not mounted into this service.

## Current limitation

V0.1.1 uses one dedicated workspace-runtime container with per-workspace directories. The production design remains **one isolated container/VM per active project/session**. Container-per-workspace scheduling, private-repository GitHub App tokens, CPU/memory quotas, network egress policy and checkpoints are the next hardening steps.
