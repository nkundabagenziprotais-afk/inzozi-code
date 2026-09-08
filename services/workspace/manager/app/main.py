from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import os
import re
import socket
import time
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
import httpx
from pydantic import BaseModel, Field, SecretStr

from app.broker_payloads import build_broker_create_workspace_payload, build_broker_push_payload

BROKER_URL = os.getenv("WORKSPACE_BROKER_URL", "http://workspace-broker:8300").rstrip("/")
BROKER_TOKEN = os.getenv("WORKSPACE_BROKER_TOKEN", "")
API_SERVICE_HOST = os.getenv("WORKSPACE_API_SERVICE_HOST", "api")
TTL_SECONDS = int(os.getenv("WORKSPACE_TTL_SECONDS", "28800"))

GITHUB_HTTPS_RE = re.compile(r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?$")
WORKSPACE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
SAFE_PUSH_BRANCH_RE = re.compile(r"^(feature|fix|ui|hotfix|deploy)/[a-z0-9][a-z0-9._-]{2,80}$")


class CreateWorkspaceRequest(BaseModel):
    repository_url: str
    ref: str | None = Field(default=None, max_length=160)
    git_token: SecretStr | None = Field(default=None, repr=False)


class PushRequest(BaseModel):
    branch: str
    expected_head: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    git_token: SecretStr = Field(repr=False)


def _workspace_id(value: str) -> str:
    if not WORKSPACE_ID_RE.fullmatch(value):
        raise ValueError("Invalid workspace id")
    return value


def _runtime_name(workspace_id: str) -> str:
    return f"inzozi-ws-{_workspace_id(workspace_id)}"


def _resolved_api_ips() -> set[str]:
    addresses: set[str] = {"127.0.0.1", "::1"}
    try:
        for item in socket.getaddrinfo(API_SERVICE_HOST, None, type=socket.SOCK_STREAM):
            addresses.add(item[4][0])
    except socket.gaierror:
        pass
    return addresses


def _require_api_client(request: Request) -> None:
    host = request.client.host if request.client else ""
    if host not in _resolved_api_ips():
        raise HTTPException(status_code=403, detail="Workspace manager accepts control requests from the API service only")


def _broker_headers() -> dict[str, str]:
    if len(BROKER_TOKEN) < 32:
        raise HTTPException(status_code=503, detail="Workspace broker authentication is not configured")
    return {"Authorization": f"Bearer {BROKER_TOKEN}"}


async def _broker_request(method: str, path: str, *, json: dict | None = None) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=190.0) as client:
            response = await client.request(
                method,
                f"{BROKER_URL}{path}",
                json=json,
                headers=_broker_headers(),
            )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=503, detail="Workspace broker is unavailable") from exc
    if response.status_code == 204:
        return None
    try:
        payload = response.json()
    except ValueError:
        payload = {"detail": "Workspace broker returned a non-JSON response"}
    if response.is_error:
        raise HTTPException(status_code=response.status_code, detail=payload.get("detail", payload))
    return payload


async def _wait_for_runtime(workspace_id: str, timeout_seconds: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    url = f"http://{_runtime_name(workspace_id)}:8100/health"
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                response = await client.get(url)
            if response.status_code == 200:
                return
        except (httpx.RequestError, ValueError) as exc:
            last_error = exc
        await asyncio.sleep(0.5)
    try:
        await _broker_request("DELETE", f"/v1/workspaces/{workspace_id}")
    except HTTPException:
        pass
    raise HTTPException(status_code=503, detail="Dedicated workspace runtime did not become healthy") from last_error


async def _proxy_to_runtime(workspace_id: str, subpath: str, request: Request) -> Response:
    url = f"http://{_runtime_name(workspace_id)}:8100/v1/workspaces/{workspace_id}/{subpath}"
    body = await request.body()
    headers: dict[str, str] = {}
    if content_type := request.headers.get("content-type"):
        headers["content-type"] = content_type
    try:
        async with httpx.AsyncClient(timeout=190.0) as client:
            response = await client.request(
                request.method,
                url,
                params=request.query_params,
                content=body if body else None,
                headers=headers,
            )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=503, detail="Dedicated workspace runtime is unavailable") from exc

    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and response.status_code < 400:
        quota = await _broker_request("POST", f"/v1/workspaces/{workspace_id}/quota/enforce") or {}
        if quota.get("exceeded") is True:
            raise HTTPException(
                status_code=507,
                detail="Workspace exceeded its disk quota and was cleaned up to protect staging capacity",
            )

    response_headers: dict[str, str] = {}
    if content_type := response.headers.get("content-type"):
        response_headers["content-type"] = content_type
    return Response(content=response.content, status_code=response.status_code, headers=response_headers)


app = FastAPI(
    title="Inzozi Code Workspace Manager",
    version="0.3.0",
    description="Unprivileged manager for brokered, dedicated Inzozi Code workspaces",
)


@app.get("/health")
async def health() -> dict:
    if len(BROKER_TOKEN) < 32:
        raise HTTPException(status_code=503, detail="Workspace broker authentication is not configured")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{BROKER_URL}/health")
    except httpx.RequestError as exc:
        raise HTTPException(status_code=503, detail="Workspace broker is unavailable") from exc
    if response.status_code != 200:
        raise HTTPException(status_code=503, detail="Workspace broker is not ready")
    try:
        broker_health = response.json()
    except ValueError:
        broker_health = {}
    return {
        "status": "ok",
        "service": "workspace-manager",
        "mode": "brokered-dedicated-workspaces",
        "runtime_egress": "denied",
        "docker_socket": "absent",
        "disk_quota": broker_health.get("disk_quota", "unknown"),
    }


@app.get("/v1/isolation/status")
async def isolation_status(request: Request) -> dict:
    _require_api_client(request)
    payload = await _broker_request("GET", "/v1/isolation/status") or {}
    payload["manager_docker_socket"] = False
    payload["manager_privilege"] = "unprivileged"
    payload["control_plane"] = "narrow-docker-broker"
    return payload


@app.post("/v1/workspaces", status_code=201)
async def create_workspace(payload: CreateWorkspaceRequest, request: Request) -> dict:
    _require_api_client(request)
    if not GITHUB_HTTPS_RE.fullmatch(payload.repository_url):
        raise HTTPException(status_code=400, detail="Dedicated workspaces accept GitHub HTTPS repository URLs only")

    workspace_id = uuid.uuid4().hex
    expires_at = int(time.time()) + TTL_SECONDS
    broker_payload = build_broker_create_workspace_payload(
        workspace_id=workspace_id,
        repository_url=payload.repository_url,
        ref=payload.ref,
        git_token=payload.git_token.get_secret_value() if payload.git_token else None,
        expires_at=expires_at,
    )
    broker_result = await _broker_request("POST", "/v1/workspaces", json=broker_payload) or {}
    await _wait_for_runtime(workspace_id)
    return {
        "workspace_id": workspace_id,
        "repository_url": payload.repository_url,
        "ref": payload.ref,
        "status": "ready",
        "execution_isolation": "dedicated-container",
        "control_plane": "narrow-docker-broker",
        "runtime_egress": "denied",
        "disk_quota": broker_result.get("disk_quota", "unknown"),
        "disk_limit_bytes": broker_result.get("disk_limit_bytes"),
        "expires_at": datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat(),
    }


@app.post("/v1/workspaces/{workspace_id}/git/push")
async def git_push(workspace_id: str, payload: PushRequest, request: Request) -> dict:
    _require_api_client(request)
    try:
        _workspace_id(workspace_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid workspace id") from exc
    if not SAFE_PUSH_BRANCH_RE.fullmatch(payload.branch):
        raise HTTPException(status_code=400, detail="Remote push is limited to safe Inzozi Code branches")
    result = await _broker_request(
        "POST",
        f"/v1/workspaces/{workspace_id}/git/push",
        json=build_broker_push_payload(
            branch=payload.branch,
            expected_head=payload.expected_head,
            git_token=payload.git_token.get_secret_value(),
        ),
    )
    return result or {}


@app.delete("/v1/workspaces/{workspace_id}", status_code=204)
async def destroy_workspace(workspace_id: str, request: Request) -> None:
    _require_api_client(request)
    try:
        _workspace_id(workspace_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid workspace id") from exc
    await _broker_request("DELETE", f"/v1/workspaces/{workspace_id}")


@app.api_route(
    "/v1/workspaces/{workspace_id}/{subpath:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
)
async def proxy_workspace(workspace_id: str, subpath: str, request: Request) -> Response:
    _require_api_client(request)
    try:
        _workspace_id(workspace_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid workspace id") from exc
    return await _proxy_to_runtime(workspace_id, subpath, request)
