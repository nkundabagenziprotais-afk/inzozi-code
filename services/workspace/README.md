# Workspace Manager (V0.2 implementation target)

The workspace manager is the security boundary between Aquila and executable project code.

## Rules

- One project/session runs in an isolated, non-root container.
- The agent never receives access to the Docker host socket.
- CPU, memory, process count, disk and execution time are limited.
- Production credentials are not mounted into development workspaces.
- Network egress is restricted by policy.
- Destructive commands are denied or require explicit approval depending on environment.
- Every tool call is written to the audit log.

## Planned tool contract

- `workspace.create`
- `workspace.clone_repository`
- `workspace.read_file`
- `workspace.search`
- `workspace.apply_patch`
- `workspace.run_command`
- `workspace.run_tests`
- `workspace.start_preview`
- `workspace.git_diff`
- `workspace.checkpoint`
- `workspace.destroy`
