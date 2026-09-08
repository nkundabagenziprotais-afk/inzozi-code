from __future__ import annotations


def build_broker_create_workspace_payload(
    *,
    workspace_id: str,
    repository_url: str,
    ref: str | None,
    git_token: str | None,
    expires_at: int,
) -> dict:
    """Manager → broker create payload. Identity secrets must never appear here."""
    return {
        "workspace_id": workspace_id,
        "repository_url": repository_url,
        "ref": ref,
        "git_token": git_token,
        "expires_at": expires_at,
    }


def build_broker_push_payload(*, branch: str, expected_head: str, git_token: str) -> dict:
    return {
        "branch": branch,
        "expected_head": expected_head,
        "git_token": git_token,
    }
