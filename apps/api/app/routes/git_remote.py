from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid

from fastapi import APIRouter, HTTPException, Request

from app.integrations.github_app import GitHubAppError, create_installation_token, default_installation_for
from app.routes.workspace import SAFE_BRANCH_RE, _request, _review_snapshot

router = APIRouter(prefix="/v1/workspaces", tags=["git-remote"])
PUSH_APPROVALS: dict[str, dict] = {}


def _clean_push_approvals() -> None:
    now = datetime.now(timezone.utc)
    expired = [
        approval_id
        for approval_id, approval in PUSH_APPROVALS.items()
        if not isinstance(approval.get("expires_at"), datetime) or approval["expires_at"] <= now
    ]
    for approval_id in expired:
        PUSH_APPROVALS.pop(approval_id, None)


@router.post("/{workspace_id}/git/push/prepare", status_code=201)
async def prepare_remote_push(workspace_id: str) -> dict:
    _clean_push_approvals()
    snapshot = await _review_snapshot(workspace_id)
    if not SAFE_BRANCH_RE.fullmatch(snapshot["branch"]):
        raise HTTPException(status_code=409, detail="Remote push requires a safe Inzozi Code branch")
    if snapshot["dirty"]:
        raise HTTPException(status_code=409, detail="Commit all reviewed changes before preparing remote push")

    metadata = await _request("GET", f"/v1/workspaces/{workspace_id}/metadata") or {}
    repository_url = metadata.get("repository_url")
    if not isinstance(repository_url, str):
        raise HTTPException(status_code=409, detail="Workspace repository metadata is unavailable")

    try:
        installation_id = default_installation_for(repository_url)
    except GitHubAppError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if installation_id is None:
        raise HTTPException(
            status_code=409,
            detail="Remote push is not configured for this repository owner. Install/configure the Inzozi Code GitHub App first.",
        )

    approval_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=10)
    PUSH_APPROVALS[approval_id] = {
        "workspace_id": workspace_id,
        "repository_url": repository_url,
        "installation_id": installation_id,
        "branch": snapshot["branch"],
        "commit_sha": snapshot["head"],
        "created_at": now,
        "expires_at": expires_at,
    }
    return {
        "approval_id": approval_id,
        "repository_url": repository_url,
        "branch": snapshot["branch"],
        "commit_sha": snapshot["head"],
        "expires_at": expires_at.isoformat(),
        "requires_human_approval": True,
        "force_push": False,
        "pull_request_created": False,
    }


@router.post("/{workspace_id}/git/push/approve")
async def approve_remote_push(workspace_id: str, request: Request) -> dict:
    _clean_push_approvals()
    body = await request.json()
    approval_id = body.get("approval_id")
    if not isinstance(approval_id, str):
        raise HTTPException(status_code=400, detail="approval_id is required")

    approval = PUSH_APPROVALS.get(approval_id)
    if approval is None or approval.get("workspace_id") != workspace_id:
        raise HTTPException(status_code=404, detail="Push approval is missing or expired")

    snapshot = await _review_snapshot(workspace_id)
    if snapshot["dirty"]:
        PUSH_APPROVALS.pop(approval_id, None)
        raise HTTPException(status_code=409, detail="Working tree changed after push review. Prepare a new push approval.")
    if snapshot["branch"] != approval["branch"] or snapshot["head"] != approval["commit_sha"]:
        PUSH_APPROVALS.pop(approval_id, None)
        raise HTTPException(status_code=409, detail="Branch or commit changed after push review. Prepare a new push approval.")

    try:
        token = await create_installation_token(
            approval["installation_id"],
            approval["repository_url"],
            contents_permission="write",
        )
    except GitHubAppError as exc:
        raise HTTPException(
            status_code=502,
            detail=(
                "Unable to obtain a GitHub write token. Confirm the Inzozi Code GitHub App has "
                "Contents: Read and write permission for this repository."
            ),
        ) from exc

    try:
        result = await _request(
            "POST",
            f"/v1/workspaces/{workspace_id}/git/push",
            json={
                "branch": approval["branch"],
                "expected_head": approval["commit_sha"],
                "git_token": token,
            },
        ) or {}
    finally:
        PUSH_APPROVALS.pop(approval_id, None)

    return {
        "status": result.get("status", "pushed"),
        "repository_url": approval["repository_url"],
        "branch": approval["branch"],
        "commit_sha": approval["commit_sha"],
        "forced": False,
        "pull_request_created": False,
    }
