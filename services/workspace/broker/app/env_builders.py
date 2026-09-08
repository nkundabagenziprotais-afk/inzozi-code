from __future__ import annotations


def quota_helper_environment(
    *,
    workspace_id: str | None = None,
    limit_bytes: int | None = None,
) -> dict[str, str]:
    environment: dict[str, str] = {}
    if workspace_id is not None:
        environment["WORKSPACE_ID"] = workspace_id
    if limit_bytes is not None:
        environment["WORKSPACE_DISK_LIMIT_BYTES"] = str(limit_bytes)
    return environment


def bootstrap_helper_environment(
    *,
    repository_url: str,
    ref: str,
    git_token: str,
) -> dict[str, str]:
    return {
        "WORKSPACE_REPOSITORY_URL": repository_url,
        "WORKSPACE_REF": ref,
        "WORKSPACE_GIT_TOKEN": git_token,
        "WORKSPACE_OWNER_UID": "10002",
        "WORKSPACE_OWNER_GID": "10002",
    }


def runtime_container_environment(
    *,
    max_file_bytes: str,
    max_output_bytes: str,
    max_search_file_bytes: str,
    max_checkpoint_bytes: str,
) -> dict[str, str]:
    return {
        "WORKSPACE_ROOT": "/workspaces",
        "WORKSPACE_MAX_FILE_BYTES": max_file_bytes,
        "WORKSPACE_MAX_OUTPUT_BYTES": max_output_bytes,
        "WORKSPACE_MAX_SEARCH_FILE_BYTES": max_search_file_bytes,
        "WORKSPACE_MAX_CHECKPOINT_BYTES": max_checkpoint_bytes,
    }


def push_helper_environment(*, branch: str, expected_head: str, git_token: str) -> dict[str, str]:
    return {
        "WORKSPACE_BRANCH": branch,
        "WORKSPACE_EXPECTED_HEAD": expected_head,
        "WORKSPACE_GIT_TOKEN": git_token,
    }
