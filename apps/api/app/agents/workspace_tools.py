from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

import httpx
from agents import RunContextWrapper
from agents.decorators import tool


MUTATING_MODES = {"build", "debug"}
APPROVED_ACTIONS = {
    "python_compile",
    "python_tests",
    "node_build",
    "node_test",
    "node_lint",
    "php_tests",
    "composer_validate",
}


@dataclass
class AquilaContext:
    workspace_id: str | None
    workspace_service_url: str
    project_name: str
    mode: str
    selected_provider: str
    allowed_actions: frozenset[str]


def allowed_actions_for_mode(mode: str) -> frozenset[str]:
    return frozenset(APPROVED_ACTIONS if mode in MUTATING_MODES else ())


def _workspace_path(context: AquilaContext, suffix: str) -> str:
    if not context.workspace_id:
        raise ValueError("No guarded workspace is connected to this agent run")
    return f"/v1/workspaces/{context.workspace_id}{suffix}"


async def workspace_request(
    context: AquilaContext,
    method: str,
    suffix: str,
    *,
    params: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
    timeout: float = 190.0,
) -> dict[str, Any] | None:
    url = f"{context.workspace_service_url.rstrip('/')}{_workspace_path(context, suffix)}"
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.request(method, url, params=params, json=payload)
    if response.status_code == 204:
        return None
    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError("Workspace runtime returned a non-JSON response") from exc
    if response.is_error:
        detail = data.get("detail", "Workspace operation failed") if isinstance(data, dict) else "Workspace operation failed"
        raise RuntimeError(str(detail))
    return data


async def create_preflight_checkpoint(context: AquilaContext, label: str) -> str | None:
    if not context.workspace_id:
        return None
    payload = await workspace_request(context, "POST", "/checkpoints", payload={"label": label})
    if not payload:
        return None
    checkpoint_id = payload.get("checkpoint_id")
    return checkpoint_id if isinstance(checkpoint_id, str) else None


async def fetch_git_diff(context: AquilaContext) -> str:
    if not context.workspace_id:
        return ""
    payload = await workspace_request(context, "GET", "/git/diff") or {}
    return str(payload.get("output", ""))


@tool(timeout=35.0)
async def search_repository(ctx: RunContextWrapper[AquilaContext], query: str, limit: int = 40) -> str:
    """Search UTF-8 source text in the connected repository.

    Args:
        query: Exact text fragment to search for. Use focused identifiers or route/model names.
        limit: Maximum result count, from 1 to 80.
    """
    limit = min(max(limit, 1), 80)
    payload = await workspace_request(ctx.context, "GET", "/search", params={"q": query, "limit": limit})
    return json.dumps(payload or {"results": []}, ensure_ascii=False)


@tool(timeout=35.0)
async def read_repository_file(ctx: RunContextWrapper[AquilaContext], path: str) -> str:
    """Read one UTF-8 repository file and return its content plus SHA-256 digest.

    Args:
        path: Repository-relative path. Never use an absolute path.
    """
    encoded_path = "/".join(httpx.URL(path).path.split("/"))
    payload = await workspace_request(ctx.context, "GET", f"/files/{encoded_path}")
    return json.dumps(payload or {}, ensure_ascii=False)


@tool(timeout=35.0)
async def inspect_git_status(ctx: RunContextWrapper[AquilaContext]) -> str:
    """Return the guarded workspace Git status without changing the repository."""
    payload = await workspace_request(ctx.context, "GET", "/git/status")
    return str((payload or {}).get("output", ""))


@tool(timeout=35.0)
async def inspect_git_diff(ctx: RunContextWrapper[AquilaContext]) -> str:
    """Return the current uncommitted Git diff without changing the repository."""
    payload = await workspace_request(ctx.context, "GET", "/git/diff")
    return str((payload or {}).get("output", ""))


@tool(timeout=40.0)
async def create_checkpoint(ctx: RunContextWrapper[AquilaContext], label: str = "Aquila checkpoint") -> str:
    """Create a bounded working-tree checkpoint before a risky group of edits."""
    if ctx.context.mode not in MUTATING_MODES:
        return "Denied: checkpoints are only available in Build or Debug mode."
    payload = await workspace_request(ctx.context, "POST", "/checkpoints", payload={"label": label[:120]})
    return json.dumps(payload or {}, ensure_ascii=False)


@tool(timeout=40.0)
async def write_repository_file(
    ctx: RunContextWrapper[AquilaContext],
    path: str,
    content: str,
    expected_sha256: str | None = None,
) -> str:
    """Write one repository file through the guarded workspace API.

    Read an existing file first and pass its SHA-256 digest as expected_sha256. Omit the digest only
    when intentionally creating a new file. This tool is unavailable outside Build or Debug mode.

    Args:
        path: Repository-relative path to write.
        content: Complete UTF-8 replacement content.
        expected_sha256: Digest from the most recent read for optimistic locking.
    """
    if ctx.context.mode not in MUTATING_MODES:
        return "Denied: file writes are only available in Build or Debug mode."
    encoded_path = "/".join(httpx.URL(path).path.split("/"))
    payload = await workspace_request(
        ctx.context,
        "PUT",
        f"/files/{encoded_path}",
        payload={"content": content, "expected_sha256": expected_sha256},
    )
    return json.dumps(payload or {}, ensure_ascii=False)


@tool(timeout=190.0)
async def run_guarded_action(ctx: RunContextWrapper[AquilaContext], action: str) -> str:
    """Run one named, pre-approved build or test recipe in the guarded workspace.

    Args:
        action: One of python_compile, python_tests, node_build, node_test, node_lint, php_tests,
            or composer_validate. Arbitrary shell commands are never accepted.
    """
    if action not in ctx.context.allowed_actions:
        return f"Denied: action {action!r} is not approved for {ctx.context.mode.upper()} mode."
    payload = await workspace_request(ctx.context, "POST", "/actions", payload={"action": action})
    return json.dumps(payload or {}, ensure_ascii=False)


def tools_for_mode(mode: str, has_workspace: bool) -> list:
    if not has_workspace:
        return []
    read_tools = [search_repository, read_repository_file, inspect_git_status, inspect_git_diff]
    if mode not in MUTATING_MODES:
        return read_tools
    return [*read_tools, create_checkpoint, write_repository_file, run_guarded_action]
