from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import re
import uuid

from fastapi import APIRouter, HTTPException, Request
import httpx

from app.core.config import get_settings
from app.integrations.github_app import (
    GitHubAppError,
    create_installation_token,
    default_installation_for,
    github_app_configured,
)
from app.security.auth import AuthPrincipal
from app.security.workspace_ownership import list_accessible_active_workspaces

router = APIRouter(prefix="/v1/workspaces", tags=["workspaces"])
settings = get_settings()

PUBLIC_ACTIONS = {
    "git_status",
    "git_diff",
    "git_branch",
    "git_log",
    "python_compile",
    "python_tests",
    "node_build",
    "node_test",
    "node_lint",
    "php_tests",
    "composer_validate",
}
PROTECTED_BRANCHES = {"main", "master", "production", "prod"}
SAFE_BRANCH_RE = re.compile(r"^(feature|fix|ui|hotfix|deploy)/[a-z0-9][a-z0-9._-]{2,80}$")
COMMIT_APPROVALS: dict[str, dict] = {}


async def _request(method: str, path: str, *, json: dict | None = None, params: dict | None = None) -> dict | None:
    url = f"{settings.workspace_service_url.rstrip('/')}{path}"
    try:
        async with httpx.AsyncClient(timeout=190.0) as client:
            response = await client.request(method, url, json=json, params=params)
    except httpx.RequestError as exc:
        raise HTTPException(status_code=503, detail="Workspace runtime is unavailable") from exc
    if response.status_code == 204:
        return None
    try:
        payload = response.json()
    except ValueError:
        payload = {"detail": "Workspace runtime returned a non-JSON response"}
    if response.is_error:
        raise HTTPException(status_code=response.status_code, detail=payload.get("detail", payload))
    return payload


async def _action(workspace_id: str, action: str) -> dict:
    payload = await _request("POST", f"/v1/workspaces/{workspace_id}/actions", json={"action": action})
    return payload or {}


def _action_output(payload: dict) -> str:
    value = payload.get("output", "")
    return value if isinstance(value, str) else ""


def _changed_paths(status_output: str) -> list[str]:
    paths: list[str] = []
    for line in status_output.splitlines():
        if not line or line.startswith("##") or len(line) < 4:
            continue
        path = line[3:].strip().strip('"')
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[-1].strip().strip('"')
        if path:
            paths.append(path.replace("\\", "/"))
    return paths


def _secret_bearing_path(path: str) -> bool:
    normalized = path.lower().strip("/")
    parts = [part for part in normalized.split("/") if part]
    basename = parts[-1] if parts else normalized
    if basename == ".env":
        return True
    if basename.startswith(".env.") and basename != ".env.example":
        return True
    if basename in {"id_rsa", "id_ed25519", "credentials.json", "service-account.json"}:
        return True
    if basename.endswith((".pem", ".p12", ".pfx", ".key")):
        return True
    if "secrets" in parts or ".secrets" in parts:
        return True
    return False


def _fingerprint(branch: str, head: str, status: str, diff: str) -> str:
    material = "\n---BRANCH---\n".join((head, branch)) + "\n---STATUS---\n" + status + "\n---DIFF---\n" + diff
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _clean_approvals() -> None:
    now = datetime.now(timezone.utc)
    expired = []
    for approval_id, approval in COMMIT_APPROVALS.items():
        expires_at = approval.get("expires_at")
        if not isinstance(expires_at, datetime) or expires_at <= now:
            expired.append(approval_id)
    for approval_id in expired:
        COMMIT_APPROVALS.pop(approval_id, None)


async def _review_snapshot(workspace_id: str, *, include_intent_add: bool = False) -> dict:
    if include_intent_add:
        intent = await _action(workspace_id, "git_intent_add")
        if intent.get("exit_code") != 0:
            raise HTTPException(status_code=409, detail="Unable to prepare new files for review")

    branch_payload = await _action(workspace_id, "git_branch")
    head_payload = await _action(workspace_id, "git_head")
    status_payload = await _action(workspace_id, "git_status")
    diff_payload = await _action(workspace_id, "git_diff_review")

    branch = _action_output(branch_payload).strip()
    head = _action_output(head_payload).strip()
    status = _action_output(status_payload)
    diff = _action_output(diff_payload)
    if not branch or not head:
        raise HTTPException(status_code=409, detail="Workspace Git state is incomplete")

    changed_paths = _changed_paths(status)
    return {
        "branch": branch,
        "head": head,
        "status": status,
        "diff": diff,
        "fingerprint": _fingerprint(branch, head, status, diff),
        "protected_branch": branch in PROTECTED_BRANCHES,
        "changed_paths": changed_paths,
        "dirty": bool(changed_paths),
    }


async def _stage_tree_hash(workspace_id: str) -> str:
    stage = await _action(workspace_id, "git_stage_all")
    if stage.get("exit_code") != 0:
        raise HTTPException(status_code=409, detail=_action_output(stage) or "Unable to stage reviewed changes")
    tree = await _action(workspace_id, "git_write_tree")
    tree_hash = _action_output(tree).strip()
    if tree.get("exit_code") != 0 or not re.fullmatch(r"[0-9a-f]{40,64}", tree_hash):
        await _action(workspace_id, "git_unstage_all")
        raise HTTPException(status_code=409, detail="Unable to calculate reviewed Git tree")
    return tree_hash


async def _unstage_best_effort(workspace_id: str) -> None:
    try:
        await _action(workspace_id, "git_unstage_all")
    except HTTPException:
        pass


@router.get("/runtime")
async def runtime_status() -> dict:
    payload = await _request("GET", "/health") or {"status": "unknown"}
    payload["github_app_configured"] = github_app_configured()
    return payload


@router.get("/recovery")
async def list_recoverable_workspaces(request: Request) -> dict:
    principal = getattr(request.state, "principal", None)
    if not isinstance(principal, AuthPrincipal):
        raise HTTPException(status_code=401, detail="Authentication required")

    ownerships = list_accessible_active_workspaces(principal)
    workspaces: list[dict] = []
    for ownership in ownerships:
        item = {
            "workspace_id": ownership.workspace_id,
            "repository_url": ownership.repository_url,
            "ref": None,
            "created_at": ownership.created_at.isoformat(),
            "runtime_status": "unavailable",
        }
        try:
            metadata = await _request("GET", f"/v1/workspaces/{ownership.workspace_id}/metadata") or {}
        except HTTPException:
            workspaces.append(item)
            continue

        item["runtime_status"] = "ready"
        ref = metadata.get("ref") if isinstance(metadata, dict) else None
        if isinstance(ref, str):
            cleaned = ref.strip()
            item["ref"] = cleaned or None
        workspaces.append(item)

    return {"workspaces": workspaces}


@router.post("")
async def create_workspace(request: Request) -> dict:
    body = await request.json()
    repository_url = body.get("repository_url")
    if not isinstance(repository_url, str):
        raise HTTPException(status_code=400, detail="repository_url is required")

    installation_id = body.pop("installation_id", None)
    try:
        if installation_id is None:
            installation_id = default_installation_for(repository_url)
        elif not isinstance(installation_id, int) or installation_id <= 0:
            raise HTTPException(status_code=400, detail="installation_id must be a positive integer")

        if installation_id is not None:
            body["git_token"] = await create_installation_token(installation_id, repository_url)
    except GitHubAppError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    payload = await _request("POST", "/v1/workspaces", json=body)
    return payload or {}


@router.get("/{workspace_id}/tree")
async def tree(workspace_id: str, path: str = "") -> dict:
    payload = await _request("GET", f"/v1/workspaces/{workspace_id}/tree", params={"path": path})
    return payload or {}


@router.get("/{workspace_id}/search")
async def search_workspace(workspace_id: str, q: str, limit: int = 80) -> dict:
    payload = await _request("GET", f"/v1/workspaces/{workspace_id}/search", params={"q": q, "limit": limit})
    return payload or {"results": []}


@router.get("/{workspace_id}/files/{file_path:path}")
async def read_file(workspace_id: str, file_path: str) -> dict:
    payload = await _request("GET", f"/v1/workspaces/{workspace_id}/files/{file_path}")
    return payload or {}


@router.put("/{workspace_id}/files/{file_path:path}")
async def write_file(workspace_id: str, file_path: str, request: Request) -> dict:
    payload = await _request("PUT", f"/v1/workspaces/{workspace_id}/files/{file_path}", json=await request.json())
    return payload or {}


@router.post("/{workspace_id}/actions")
async def run_action(workspace_id: str, request: Request) -> dict:
    body = await request.json()
    action = body.get("action")
    if action not in PUBLIC_ACTIONS:
        raise HTTPException(status_code=403, detail="This action is reserved for an approval-gated workflow")
    payload = await _request("POST", f"/v1/workspaces/{workspace_id}/actions", json=body)
    return payload or {}


@router.get("/{workspace_id}/git/status")
async def git_status(workspace_id: str) -> dict:
    payload = await _request("GET", f"/v1/workspaces/{workspace_id}/git/status")
    return payload or {}


@router.get("/{workspace_id}/git/diff")
async def git_diff(workspace_id: str) -> dict:
    payload = await _request("GET", f"/v1/workspaces/{workspace_id}/git/diff")
    return payload or {}


@router.get("/{workspace_id}/git/review")
async def git_review(workspace_id: str) -> dict:
    return await _review_snapshot(workspace_id)


@router.post("/{workspace_id}/git/branches", status_code=201)
async def create_git_branch(workspace_id: str, request: Request) -> dict:
    body = await request.json()
    branch_name = body.get("branch_name")
    if not isinstance(branch_name, str) or not SAFE_BRANCH_RE.fullmatch(branch_name):
        raise HTTPException(
            status_code=400,
            detail="Use a safe branch such as feature/name, fix/name, ui/name, hotfix/name, or deploy/name",
        )
    result = await _action(workspace_id, f"git_create_branch:{branch_name}")
    if result.get("exit_code") != 0:
        raise HTTPException(status_code=409, detail=_action_output(result) or "Unable to create branch")
    for approval_id in [key for key, item in COMMIT_APPROVALS.items() if item.get("workspace_id") == workspace_id]:
        COMMIT_APPROVALS.pop(approval_id, None)
    return {"branch": branch_name, "status": "created"}


@router.post("/{workspace_id}/git/commit/prepare", status_code=201)
async def prepare_git_commit(workspace_id: str, request: Request) -> dict:
    _clean_approvals()
    body = await request.json()
    message = body.get("message")
    if not isinstance(message, str):
        raise HTTPException(status_code=400, detail="Commit message is required")
    message = message.strip()
    if not 5 <= len(message) <= 120 or "\n" in message or "\r" in message:
        raise HTTPException(status_code=400, detail="Commit message must be 5 to 120 characters on one line")

    snapshot = await _review_snapshot(workspace_id, include_intent_add=True)
    if snapshot["protected_branch"]:
        await _unstage_best_effort(workspace_id)
        raise HTTPException(status_code=409, detail="Create a feature/fix/ui/hotfix/deploy branch before committing")
    if not snapshot["dirty"]:
        await _unstage_best_effort(workspace_id)
        raise HTTPException(status_code=409, detail="There are no working-tree changes to commit")

    blocked = [path for path in snapshot["changed_paths"] if _secret_bearing_path(path)]
    if blocked:
        await _unstage_best_effort(workspace_id)
        raise HTTPException(status_code=409, detail=f"Commit blocked because sensitive paths changed: {', '.join(blocked[:5])}")

    diff_check = await _action(workspace_id, "git_diff_check")
    if diff_check.get("exit_code") != 0:
        await _unstage_best_effort(workspace_id)
        raise HTTPException(status_code=409, detail=_action_output(diff_check) or "Git diff validation failed")

    tree_hash = await _stage_tree_hash(workspace_id)
    unstage = await _action(workspace_id, "git_unstage_all")
    if unstage.get("exit_code") != 0:
        raise HTTPException(status_code=409, detail="Unable to restore the workspace index after commit preparation")

    approval_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=15)
    COMMIT_APPROVALS[approval_id] = {
        "workspace_id": workspace_id,
        "message": message,
        "branch": snapshot["branch"],
        "head": snapshot["head"],
        "fingerprint": snapshot["fingerprint"],
        "tree_hash": tree_hash,
        "created_at": now,
        "expires_at": expires_at,
    }
    return {
        "approval_id": approval_id,
        "branch": snapshot["branch"],
        "head": snapshot["head"],
        "message": message,
        "status": snapshot["status"],
        "diff": snapshot["diff"],
        "changed_paths": snapshot["changed_paths"],
        "tree_hash": tree_hash,
        "expires_at": expires_at.isoformat(),
        "requires_human_approval": True,
    }


@router.post("/{workspace_id}/git/commit/approve")
async def approve_git_commit(workspace_id: str, request: Request) -> dict:
    _clean_approvals()
    body = await request.json()
    approval_id = body.get("approval_id")
    if not isinstance(approval_id, str):
        raise HTTPException(status_code=400, detail="approval_id is required")
    approval = COMMIT_APPROVALS.get(approval_id)
    if approval is None or approval.get("workspace_id") != workspace_id:
        raise HTTPException(status_code=404, detail="Commit approval is missing or expired")

    current = await _review_snapshot(workspace_id, include_intent_add=True)
    if (
        current["branch"] != approval["branch"]
        or current["head"] != approval["head"]
        or current["fingerprint"] != approval["fingerprint"]
    ):
        await _unstage_best_effort(workspace_id)
        COMMIT_APPROVALS.pop(approval_id, None)
        raise HTTPException(status_code=409, detail="Workspace changed after review. Prepare a new commit approval.")

    blocked = [path for path in current["changed_paths"] if _secret_bearing_path(path)]
    if blocked:
        await _unstage_best_effort(workspace_id)
        COMMIT_APPROVALS.pop(approval_id, None)
        raise HTTPException(status_code=409, detail="Commit approval invalidated by a sensitive-path change")

    tree_hash = await _stage_tree_hash(workspace_id)
    if tree_hash != approval.get("tree_hash"):
        await _unstage_best_effort(workspace_id)
        COMMIT_APPROVALS.pop(approval_id, None)
        raise HTTPException(status_code=409, detail="Staged Git tree differs from the reviewed tree. Prepare a new approval.")

    encoded = base64.urlsafe_b64encode(approval["message"].encode("utf-8")).decode("ascii").rstrip("=")
    commit = await _action(workspace_id, f"git_commit_b64:{encoded}")
    if commit.get("exit_code") != 0:
        await _unstage_best_effort(workspace_id)
        COMMIT_APPROVALS.pop(approval_id, None)
        raise HTTPException(status_code=409, detail=_action_output(commit) or "Git commit failed")

    new_head = _action_output(await _action(workspace_id, "git_head")).strip()
    COMMIT_APPROVALS.pop(approval_id, None)
    return {
        "status": "committed",
        "branch": current["branch"],
        "commit_sha": new_head,
        "tree_hash": tree_hash,
        "message": approval["message"],
        "pushed": False,
        "remote_write_enabled": False,
    }


@router.post("/{workspace_id}/checkpoints")
async def create_checkpoint(workspace_id: str, request: Request) -> dict:
    payload = await _request("POST", f"/v1/workspaces/{workspace_id}/checkpoints", json=await request.json())
    return payload or {}


@router.get("/{workspace_id}/checkpoints")
async def list_checkpoints(workspace_id: str) -> dict:
    payload = await _request("GET", f"/v1/workspaces/{workspace_id}/checkpoints")
    return payload or {"checkpoints": []}


@router.post("/{workspace_id}/checkpoints/{checkpoint_id}/restore")
async def restore_checkpoint(workspace_id: str, checkpoint_id: str) -> dict:
    payload = await _request("POST", f"/v1/workspaces/{workspace_id}/checkpoints/{checkpoint_id}/restore")
    return payload or {}


@router.delete("/{workspace_id}/checkpoints/{checkpoint_id}", status_code=204)
async def delete_checkpoint(workspace_id: str, checkpoint_id: str) -> None:
    await _request("DELETE", f"/v1/workspaces/{workspace_id}/checkpoints/{checkpoint_id}")


@router.delete("/{workspace_id}", status_code=204)
async def destroy_workspace(workspace_id: str) -> None:
    for approval_id in [key for key, item in COMMIT_APPROVALS.items() if item.get("workspace_id") == workspace_id]:
        COMMIT_APPROVALS.pop(approval_id, None)
    await _request("DELETE", f"/v1/workspaces/{workspace_id}")
