from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
import httpx

from app.core.config import get_settings

router = APIRouter(prefix="/v1/workspaces", tags=["workspaces"])
settings = get_settings()


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


@router.get("/runtime")
async def runtime_status() -> dict:
    payload = await _request("GET", "/health")
    return payload or {"status": "unknown"}


@router.post("")
async def create_workspace(request: Request) -> dict:
    payload = await _request("POST", "/v1/workspaces", json=await request.json())
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
    payload = await _request("POST", f"/v1/workspaces/{workspace_id}/actions", json=await request.json())
    return payload or {}


@router.get("/{workspace_id}/git/status")
async def git_status(workspace_id: str) -> dict:
    payload = await _request("GET", f"/v1/workspaces/{workspace_id}/git/status")
    return payload or {}


@router.get("/{workspace_id}/git/diff")
async def git_diff(workspace_id: str) -> dict:
    payload = await _request("GET", f"/v1/workspaces/{workspace_id}/git/diff")
    return payload or {}


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
    await _request("DELETE", f"/v1/workspaces/{workspace_id}")
