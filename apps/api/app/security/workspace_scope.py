from __future__ import annotations

import json
import re
import uuid

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import get_settings
from app.security.auth import AuthPrincipal
from app.security.workspace_ownership import (
    get_workspace_ownership,
    mark_workspace_deleted,
    principal_can_access,
    record_workspace_audit,
    register_workspace,
)

WORKSPACE_PATH_RE = re.compile(r"^/v1/workspaces/([0-9a-f]{32})(?:/|$)")
MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


async def _buffer_response(response) -> tuple[bytes, Response]:
    body = b"".join([chunk async for chunk in response.body_iterator])
    headers = dict(response.headers)
    headers.pop("content-length", None)
    replacement = Response(
        content=body,
        status_code=response.status_code,
        headers=headers,
        media_type=response.media_type,
        background=response.background,
    )
    return body, replacement


async def _destroy_unregistered_workspace(workspace_id: str) -> None:
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.delete(f"{settings.workspace_service_url.rstrip('/')}/v1/workspaces/{workspace_id}")
    except httpx.RequestError:
        pass


class WorkspaceOwnershipMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        settings = get_settings()
        if not settings.workspace_ownership_enforced:
            return await call_next(request)

        principal = getattr(request.state, "principal", None)
        if not isinstance(principal, AuthPrincipal):
            return JSONResponse(status_code=401, content={"detail": "Authentication required"})

        path = request.url.path
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex

        if request.method == "POST" and path == "/v1/workspaces":
            try:
                request_payload = await request.json()
            except Exception:
                request_payload = {}
            repository_url = request_payload.get("repository_url") if isinstance(request_payload, dict) else None
            response = await call_next(request)
            if response.status_code != 201:
                return response
            body, buffered = await _buffer_response(response)
            try:
                payload = json.loads(body)
                workspace_id = payload["workspace_id"]
                if not isinstance(repository_url, str) or not repository_url:
                    raise ValueError("Repository URL missing from create request")
                register_workspace(workspace_id=workspace_id, principal=principal, repository_url=repository_url)
                record_workspace_audit(
                    principal=principal,
                    workspace_id=workspace_id,
                    event_type="workspace.create",
                    outcome="allowed",
                    request_id=request_id,
                    details={"repository_url": repository_url},
                )
            except Exception:
                if isinstance(locals().get("workspace_id"), str):
                    await _destroy_unregistered_workspace(workspace_id)
                return JSONResponse(status_code=503, content={"detail": "Workspace ownership registration failed safely"})
            return buffered

        match = WORKSPACE_PATH_RE.match(path)
        if match is None:
            return await call_next(request)

        workspace_id = match.group(1)
        try:
            ownership = get_workspace_ownership(workspace_id)
        except Exception:
            return JSONResponse(status_code=503, content={"detail": "Workspace ownership registry is unavailable"})

        if ownership is None or not principal_can_access(principal, ownership):
            try:
                record_workspace_audit(
                    principal=principal,
                    workspace_id=workspace_id,
                    event_type="workspace.access",
                    outcome="denied",
                    request_id=request_id,
                    details={"method": request.method, "path": path[:240]},
                )
            except Exception:
                pass
            # Deliberately return 404 to avoid confirming another user's workspace identifier.
            return JSONResponse(status_code=404, content={"detail": "Workspace not found"})

        request.state.workspace_ownership = ownership
        response = await call_next(request)

        if request.method in MUTATING_METHODS:
            try:
                record_workspace_audit(
                    principal=principal,
                    workspace_id=workspace_id,
                    event_type="workspace.mutation",
                    outcome="allowed" if response.status_code < 400 else "rejected",
                    request_id=request_id,
                    details={"method": request.method, "path": path[:240], "status": response.status_code},
                )
            except Exception:
                # Authorization already succeeded; audit failure must not silently widen access.
                # Startup health/preflight validates durable audit connectivity separately.
                pass

        if request.method == "DELETE" and path == f"/v1/workspaces/{workspace_id}" and response.status_code == 204:
            try:
                mark_workspace_deleted(workspace_id)
            except Exception:
                return JSONResponse(status_code=503, content={"detail": "Workspace was destroyed but ownership archival failed"})

        return response
