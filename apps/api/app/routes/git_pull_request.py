from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
import uuid

from fastapi import APIRouter, HTTPException, Request

from app.integrations.github_app import (
    GitHubAppError,
    create_installation_token,
    create_or_get_draft_pull_request,
    default_installation_for,
    get_remote_branch_head,
)
from app.routes.workspace import SAFE_BRANCH_RE, _request, _review_snapshot

router = APIRouter(prefix="/v1/workspaces", tags=["git-pull-request"])
PULL_REQUEST_APPROVALS: dict[str, dict] = {}
ALLOWED_BASE_BRANCHES = {"main", "master", "develop", "development", "staging"}
_TITLE_RE = re.compile(r"^[^\r\n]{5,120}$")


def _clean_pull_request_approvals() -> None:
    now = datetime.now(timezone.utc)
    expired = [
        approval_id
        for approval_id, approval in PULL_REQUEST_APPROVALS.items()
        if not isinstance(approval.get("expires_at"), datetime) or approval["expires_at"] <= now
    ]
    for approval_id in expired:
        PULL_REQUEST_APPROVALS.pop(approval_id, None)


def _validated_request_copy(body: dict) -> tuple[str, str, str]:
    title = body.get("title")
    description = body.get("body", "")
    base_branch = body.get("base_branch", "main")
    if not isinstance(title, str) or not _TITLE_RE.fullmatch(title.strip()):
        raise HTTPException(status_code=400, detail="Pull request title must be 5 to 120 characters on one line")
    if not isinstance(description, str) or len(description) > 8000:
        raise HTTPException(status_code=400, detail="Pull request body must be at most 8000 characters")
    if not isinstance(base_branch, str) or base_branch not in ALLOWED_BASE_BRANCHES:
        raise HTTPException(
            status_code=400,
            detail="Base branch must be main, master, develop, development, or staging",
        )
    return title.strip(), description.strip(), base_branch


@router.post("/{workspace_id}/git/pull-request/prepare", status_code=201)
async def prepare_pull_request(workspace_id: str, request: Request) -> dict:
    _clean_pull_request_approvals()
    title, description, base_branch = _validated_request_copy(await request.json())
    snapshot = await _review_snapshot(workspace_id)
    if snapshot["dirty"]:
        raise HTTPException(status_code=409, detail="Commit and push all reviewed changes before preparing a pull request")
    if not SAFE_BRANCH_RE.fullmatch(snapshot["branch"]):
        raise HTTPException(status_code=409, detail="Pull requests must originate from a safe Inzozi Code branch")
    if snapshot["branch"] == base_branch:
        raise HTTPException(status_code=409, detail="Pull request head and base branches must be different")

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
            detail="Pull request creation is not configured for this repository owner.",
        )

    approval_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=10)
    PULL_REQUEST_APPROVALS[approval_id] = {
        "workspace_id": workspace_id,
        "repository_url": repository_url,
        "installation_id": installation_id,
        "branch": snapshot["branch"],
        "commit_sha": snapshot["head"],
        "title": title,
        "body": description,
        "base_branch": base_branch,
        "created_at": now,
        "expires_at": expires_at,
    }
    return {
        "approval_id": approval_id,
        "repository_url": repository_url,
        "head_branch": snapshot["branch"],
        "base_branch": base_branch,
        "commit_sha": snapshot["head"],
        "title": title,
        "body": description,
        "draft": True,
        "expires_at": expires_at.isoformat(),
        "requires_human_approval": True,
        "merge_enabled": False,
    }


@router.post("/{workspace_id}/git/pull-request/approve")
async def approve_pull_request(workspace_id: str, request: Request) -> dict:
    _clean_pull_request_approvals()
    body = await request.json()
    approval_id = body.get("approval_id")
    if not isinstance(approval_id, str):
        raise HTTPException(status_code=400, detail="approval_id is required")

    approval = PULL_REQUEST_APPROVALS.get(approval_id)
    if approval is None or approval.get("workspace_id") != workspace_id:
        raise HTTPException(status_code=404, detail="Pull request approval is missing or expired")

    snapshot = await _review_snapshot(workspace_id)
    if snapshot["dirty"] or snapshot["branch"] != approval["branch"] or snapshot["head"] != approval["commit_sha"]:
        PULL_REQUEST_APPROVALS.pop(approval_id, None)
        raise HTTPException(
            status_code=409,
            detail="Workspace changed after pull request review. Prepare a new approval.",
        )

    try:
        token = await create_installation_token(
            approval["installation_id"],
            approval["repository_url"],
            contents_permission="read",
            pull_requests_permission="write",
        )
        remote_head = await get_remote_branch_head(
            approval["repository_url"], approval["branch"], token
        )
        if remote_head != approval["commit_sha"]:
            raise HTTPException(
                status_code=409,
                detail="GitHub branch no longer matches the reviewed local commit. Push the exact commit and prepare again.",
            )
        result = await create_or_get_draft_pull_request(
            approval["repository_url"],
            branch=approval["branch"],
            base_branch=approval["base_branch"],
            title=approval["title"],
            body=approval["body"],
            token=token,
        )
    except HTTPException:
        raise
    except GitHubAppError as exc:
        raise HTTPException(
            status_code=502,
            detail=(
                "Unable to create the draft pull request. Confirm the Inzozi Code GitHub App has "
                "Pull requests: Read and write and Contents: Read permission."
            ),
        ) from exc
    finally:
        PULL_REQUEST_APPROVALS.pop(approval_id, None)

    return {
        "status": result.get("status", "created"),
        "repository_url": approval["repository_url"],
        "head_branch": approval["branch"],
        "base_branch": approval["base_branch"],
        "commit_sha": approval["commit_sha"],
        "pull_request_number": result.get("number"),
        "pull_request_url": result.get("html_url"),
        "draft": bool(result.get("draft", True)),
        "merged": False,
        "deployment_started": False,
    }
