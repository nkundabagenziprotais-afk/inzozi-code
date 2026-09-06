from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import hmac
import os
import re
import socket
import time

import docker
from docker.errors import ContainerError, DockerException, NotFound
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field, SecretStr

RUNTIME_IMAGE = os.getenv("WORKSPACE_RUNTIME_IMAGE", "inzozi-code-workspace-runtime:local")
MANAGER_SERVICE_HOST = os.getenv("WORKSPACE_MANAGER_SERVICE_HOST", "workspace-manager")
BROKER_TOKEN = os.getenv("WORKSPACE_BROKER_TOKEN", "")
EGRESS_NETWORK = os.getenv("WORKSPACE_EGRESS_NETWORK", "inzozi-workspace-egress")
MAX_ACTIVE = int(os.getenv("WORKSPACE_MAX_ACTIVE", "8"))
DISK_LIMIT_BYTES = int(os.getenv("WORKSPACE_DISK_LIMIT_BYTES", str(2 * 1024 * 1024 * 1024)))
RUNTIME_MEMORY = os.getenv("WORKSPACE_RUNTIME_MEMORY_LIMIT", "4g")
RUNTIME_NANO_CPUS = int(float(os.getenv("WORKSPACE_RUNTIME_CPUS", "2.0")) * 1_000_000_000)
RUNTIME_PIDS_LIMIT = int(os.getenv("WORKSPACE_RUNTIME_PIDS_LIMIT", "256"))
RUNTIME_TMPFS = os.getenv("WORKSPACE_RUNTIME_TMPFS", "rw,noexec,nosuid,nodev,size=268435456")
MAX_FILE_BYTES = os.getenv("WORKSPACE_MAX_FILE_BYTES", "1048576")
MAX_OUTPUT_BYTES = os.getenv("WORKSPACE_MAX_OUTPUT_BYTES", "65536")
MAX_SEARCH_FILE_BYTES = os.getenv("WORKSPACE_MAX_SEARCH_FILE_BYTES", "524288")
MAX_CHECKPOINT_BYTES = os.getenv("WORKSPACE_MAX_CHECKPOINT_BYTES", str(100 * 1024 * 1024))

GITHUB_HTTPS_RE = re.compile(r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?$")
WORKSPACE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,159}$")
SAFE_PUSH_BRANCH_RE = re.compile(r"^(feature|fix|ui|hotfix|deploy)/[a-z0-9][a-z0-9._-]{2,80}$")
HEAD_RE = re.compile(r"^[0-9a-f]{40,64}$")

LABEL_WORKSPACE = "com.inzozi.code.workspace"
LABEL_WORKSPACE_ID = "com.inzozi.code.workspace_id"
LABEL_EXPIRES_AT = "com.inzozi.code.expires_at"
LABEL_NETWORK = "com.inzozi.code.network"
LABEL_KIND = "com.inzozi.code.kind"
LABEL_ROLE = "com.inzozi.code.role"


docker_client = docker.from_env()


class CreateWorkspaceRequest(BaseModel):
    workspace_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    repository_url: str
    ref: str | None = Field(default=None, max_length=160)
    git_token: SecretStr | None = Field(default=None, repr=False)
    expires_at: int


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


def _volume_name(workspace_id: str) -> str:
    return f"inzozi-ws-vol-{_workspace_id(workspace_id)}"


def _network_name(workspace_id: str) -> str:
    return f"inzozi-ws-net-{_workspace_id(workspace_id)}"


def _broker_container():
    container_id = os.getenv("HOSTNAME", "")
    if not container_id:
        raise RuntimeError("Workspace broker container identity is unavailable")
    return docker_client.containers.get(container_id)


def _compose_project() -> str:
    labels = _broker_container().labels or {}
    return labels.get("com.docker.compose.project", "")


def _manager_container():
    project = _compose_project()
    labels = [f"{LABEL_ROLE}=workspace-manager"]
    if project:
        labels.append(f"com.docker.compose.project={project}")
    containers = docker_client.containers.list(all=True, filters={"label": labels})
    if len(containers) != 1:
        raise RuntimeError("Workspace manager container identity is ambiguous or unavailable")
    return containers[0]


def _resolved_manager_ips() -> set[str]:
    addresses: set[str] = {"127.0.0.1", "::1"}
    try:
        for item in socket.getaddrinfo(MANAGER_SERVICE_HOST, None, type=socket.SOCK_STREAM):
            addresses.add(item[4][0])
    except socket.gaierror:
        pass
    return addresses


def _require_manager(request: Request) -> None:
    if len(BROKER_TOKEN) < 32:
        raise HTTPException(status_code=503, detail="Workspace broker authentication is not configured")
    authorization = request.headers.get("authorization", "")
    expected = f"Bearer {BROKER_TOKEN}"
    if not hmac.compare_digest(authorization, expected):
        raise HTTPException(status_code=403, detail="Workspace broker rejected the caller")
    host = request.client.host if request.client else ""
    if host not in _resolved_manager_ips():
        raise HTTPException(status_code=403, detail="Workspace broker accepts requests from the workspace manager only")


def _workspace_labels(workspace_id: str, expires_at: int, network_name: str) -> dict[str, str]:
    return {
        LABEL_WORKSPACE: "true",
        LABEL_WORKSPACE_ID: workspace_id,
        LABEL_EXPIRES_AT: str(expires_at),
        LABEL_NETWORK: network_name,
        LABEL_KIND: "runtime",
    }


def _ensure_egress_network() -> None:
    try:
        network = docker_client.networks.get(EGRESS_NETWORK)
        if bool(network.attrs.get("Internal")):
            raise RuntimeError("Workspace helper egress network is unexpectedly internal")
    except NotFound:
        docker_client.networks.create(
            EGRESS_NETWORK,
            driver="bridge",
            internal=False,
            labels={LABEL_KIND: "workspace-helper-egress"},
        )


def _connect_manager_to(network) -> None:
    manager = _manager_container()
    network.reload()
    endpoints = network.attrs.get("Containers") or {}
    if manager.id not in endpoints:
        network.connect(manager)


def _recover_workspace_networks() -> None:
    try:
        manager = _manager_container()
    except RuntimeError:
        return
    for container in docker_client.containers.list(all=True, filters={"label": f"{LABEL_WORKSPACE}=true"}):
        labels = container.labels or {}
        network_name = labels.get(LABEL_NETWORK)
        if not network_name:
            continue
        try:
            network = docker_client.networks.get(network_name)
            if not bool(network.attrs.get("Internal")):
                continue
            network.reload()
            endpoints = network.attrs.get("Containers") or {}
            if manager.id not in endpoints:
                network.connect(manager)
        except DockerException:
            continue


def _active_containers():
    return docker_client.containers.list(all=True, filters={"label": f"{LABEL_WORKSPACE}=true"})


def _container_for(workspace_id: str):
    workspace_id = _workspace_id(workspace_id)
    try:
        container = docker_client.containers.get(_runtime_name(workspace_id))
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="Workspace not found") from exc
    if (container.labels or {}).get(LABEL_WORKSPACE_ID) != workspace_id:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return container


def _is_expired(container) -> bool:
    try:
        expires_at = int((container.labels or {}).get(LABEL_EXPIRES_AT, "0"))
    except ValueError:
        return True
    return expires_at <= int(time.time())


def _destroy_sync(workspace_id: str) -> None:
    workspace_id = _workspace_id(workspace_id)
    try:
        docker_client.containers.get(_runtime_name(workspace_id)).remove(force=True)
    except NotFound:
        pass

    try:
        network = docker_client.networks.get(_network_name(workspace_id))
        try:
            network.disconnect(_manager_container(), force=True)
        except (DockerException, RuntimeError):
            pass
        network.remove()
    except NotFound:
        pass

    try:
        docker_client.volumes.get(_volume_name(workspace_id)).remove(force=True)
    except NotFound:
        pass


def _assert_live_sync(workspace_id: str):
    container = _container_for(workspace_id)
    if _is_expired(container):
        _destroy_sync(workspace_id)
        raise HTTPException(status_code=410, detail="Workspace expired and was securely cleaned up")
    container.reload()
    if container.status != "running":
        raise HTTPException(status_code=503, detail="Workspace runtime is not running")
    return container


def _helper_run(
    *,
    module: str,
    environment: dict[str, str],
    volume_name: str,
    network_name: str | None,
    root_bootstrap: bool = False,
) -> bytes:
    if module not in {"app.bootstrap", "app.remote_push"}:
        raise RuntimeError("Unsupported workspace helper module")
    kwargs = {
        "image": RUNTIME_IMAGE,
        "command": ["python", "-m", module],
        "environment": environment,
        "volumes": {volume_name: {"bind": "/workspace", "mode": "rw"}},
        "read_only": True,
        "tmpfs": {"/tmp": "rw,noexec,nosuid,nodev,size=134217728"},
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "mem_limit": "1g",
        "nano_cpus": 1_000_000_000,
        "pids_limit": 128,
        "remove": True,
        "stdout": True,
        "stderr": True,
        "labels": {LABEL_KIND: "workspace-helper"},
    }
    if network_name:
        kwargs["network"] = network_name
    else:
        kwargs["network_mode"] = "none"
    if root_bootstrap:
        kwargs["user"] = "0:0"
        kwargs["cap_add"] = ["CHOWN"]
    try:
        return docker_client.containers.run(**kwargs)
    except ContainerError as exc:
        raise RuntimeError("Workspace helper action failed safely") from exc


def _volume_size_sync(volume_name: str) -> int:
    output = docker_client.containers.run(
        image=RUNTIME_IMAGE,
        command=["sh", "-lc", "du -sb /workspace | cut -f1"],
        volumes={volume_name: {"bind": "/workspace", "mode": "ro"}},
        network_mode="none",
        read_only=True,
        cap_drop=["ALL"],
        security_opt=["no-new-privileges:true"],
        mem_limit="256m",
        nano_cpus=500_000_000,
        pids_limit=64,
        remove=True,
        stdout=True,
        stderr=True,
        labels={LABEL_KIND: "workspace-quota-probe"},
    )
    try:
        return int(output.decode("utf-8").strip().splitlines()[-1])
    except (ValueError, IndexError) as exc:
        raise RuntimeError("Unable to measure workspace disk usage") from exc


def _create_workspace_sync(payload: CreateWorkspaceRequest) -> dict:
    workspace_id = _workspace_id(payload.workspace_id)
    if not GITHUB_HTTPS_RE.fullmatch(payload.repository_url):
        raise HTTPException(status_code=400, detail="Workspace broker accepts GitHub HTTPS repository URLs only")
    if payload.ref and not REF_RE.fullmatch(payload.ref):
        raise HTTPException(status_code=400, detail="Invalid Git ref")
    if payload.expires_at <= int(time.time()):
        raise HTTPException(status_code=400, detail="Workspace expiry must be in the future")
    if len(_active_containers()) >= MAX_ACTIVE:
        raise HTTPException(status_code=429, detail="Staging workspace capacity reached")

    volume_name = _volume_name(workspace_id)
    network_name = _network_name(workspace_id)

    try:
        docker_client.volumes.get(volume_name)
        raise HTTPException(status_code=409, detail="Workspace resource already exists")
    except NotFound:
        pass

    docker_client.volumes.create(
        name=volume_name,
        labels={LABEL_WORKSPACE: "true", LABEL_WORKSPACE_ID: workspace_id, LABEL_KIND: "volume"},
    )
    network = docker_client.networks.create(
        network_name,
        driver="bridge",
        internal=True,
        labels={LABEL_WORKSPACE: "true", LABEL_WORKSPACE_ID: workspace_id, LABEL_KIND: "runtime-network"},
    )

    try:
        _connect_manager_to(network)
        _helper_run(
            module="app.bootstrap",
            environment={
                "WORKSPACE_REPOSITORY_URL": payload.repository_url,
                "WORKSPACE_REF": payload.ref or "",
                "WORKSPACE_GIT_TOKEN": payload.git_token.get_secret_value() if payload.git_token else "",
                "WORKSPACE_OWNER_UID": "10002",
                "WORKSPACE_OWNER_GID": "10002",
            },
            volume_name=volume_name,
            network_name=EGRESS_NETWORK,
            root_bootstrap=True,
        )

        size_bytes = _volume_size_sync(volume_name)
        if size_bytes > DISK_LIMIT_BYTES:
            raise HTTPException(status_code=413, detail="Repository exceeds the staging workspace disk quota")

        docker_client.containers.run(
            image=RUNTIME_IMAGE,
            name=_runtime_name(workspace_id),
            hostname=f"workspace-{workspace_id[:12]}",
            detach=True,
            network=network_name,
            volumes={volume_name: {"bind": f"/workspaces/{workspace_id}", "mode": "rw"}},
            environment={
                "WORKSPACE_ROOT": "/workspaces",
                "WORKSPACE_MAX_FILE_BYTES": MAX_FILE_BYTES,
                "WORKSPACE_MAX_OUTPUT_BYTES": MAX_OUTPUT_BYTES,
                "WORKSPACE_MAX_SEARCH_FILE_BYTES": MAX_SEARCH_FILE_BYTES,
                "WORKSPACE_MAX_CHECKPOINT_BYTES": MAX_CHECKPOINT_BYTES,
            },
            read_only=True,
            tmpfs={"/tmp": RUNTIME_TMPFS},
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            mem_limit=RUNTIME_MEMORY,
            nano_cpus=RUNTIME_NANO_CPUS,
            pids_limit=RUNTIME_PIDS_LIMIT,
            restart_policy={"Name": "unless-stopped"},
            labels=_workspace_labels(workspace_id, payload.expires_at, network_name),
        )
    except Exception:
        _destroy_sync(workspace_id)
        raise

    return {
        "workspace_id": workspace_id,
        "status": "provisioned",
        "size_bytes": size_bytes,
        "network_internal": True,
    }


def _push_sync(workspace_id: str, request: PushRequest) -> dict:
    _assert_live_sync(workspace_id)
    if not SAFE_PUSH_BRANCH_RE.fullmatch(request.branch):
        raise HTTPException(status_code=400, detail="Remote push is limited to safe Inzozi Code branches")
    if not HEAD_RE.fullmatch(request.expected_head):
        raise HTTPException(status_code=400, detail="Invalid reviewed commit")
    output = _helper_run(
        module="app.remote_push",
        environment={
            "WORKSPACE_BRANCH": request.branch,
            "WORKSPACE_EXPECTED_HEAD": request.expected_head,
            "WORKSPACE_GIT_TOKEN": request.git_token.get_secret_value(),
        },
        volume_name=_volume_name(workspace_id),
        network_name=EGRESS_NETWORK,
    )
    import json

    try:
        result = json.loads(output.decode("utf-8").strip().splitlines()[-1])
    except (ValueError, IndexError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=502, detail="Remote push helper returned an invalid result") from exc
    if not isinstance(result, dict) or result.get("status") != "pushed":
        raise HTTPException(status_code=502, detail="Remote push failed safely")
    return result


def _quota_sync(workspace_id: str) -> dict:
    _assert_live_sync(workspace_id)
    size_bytes = _volume_size_sync(_volume_name(workspace_id))
    exceeded = size_bytes > DISK_LIMIT_BYTES
    if exceeded:
        _destroy_sync(workspace_id)
    return {
        "workspace_id": workspace_id,
        "size_bytes": size_bytes,
        "limit_bytes": DISK_LIMIT_BYTES,
        "exceeded": exceeded,
        "cleaned_up": exceeded,
    }


def _isolation_status_sync() -> dict:
    containers = _active_containers()
    workspaces: list[dict] = []
    socket_mounts = 0
    network_names: set[str] = set()
    all_networks_internal = True
    for container in containers:
        container.reload()
        labels = container.labels or {}
        workspace_id = labels.get(LABEL_WORKSPACE_ID, "")
        network_name = labels.get(LABEL_NETWORK, "")
        mounts = container.attrs.get("Mounts") or []
        if any(
            mount.get("Source") == "/var/run/docker.sock" or mount.get("Destination") == "/var/run/docker.sock"
            for mount in mounts
        ):
            socket_mounts += 1
        network_internal = False
        if network_name:
            try:
                network = docker_client.networks.get(network_name)
                network_internal = bool(network.attrs.get("Internal"))
                network_names.add(network_name)
            except NotFound:
                network_internal = False
        all_networks_internal = all_networks_internal and network_internal
        workspaces.append(
            {
                "workspace_id": workspace_id,
                "status": container.status,
                "network": network_name,
                "network_internal": network_internal,
                "expires_at": labels.get(LABEL_EXPIRES_AT),
            }
        )
    return {
        "status": "ok",
        "mode": "brokered-dedicated-workspaces",
        "active_workspaces": len(workspaces),
        "workspace_socket_mounts": socket_mounts,
        "dedicated_networks": len(network_names) == len(workspaces),
        "all_runtime_networks_internal": all_networks_internal,
        "runtime_egress": "denied",
        "git_helper_egress": "ephemeral-github-actions-only",
        "disk_quota_bytes": DISK_LIMIT_BYTES,
        "max_active_workspaces": MAX_ACTIVE,
        "workspaces": workspaces,
    }


async def _janitor() -> None:
    while True:
        await asyncio.sleep(60)
        for container in await asyncio.to_thread(_active_containers):
            if _is_expired(container):
                workspace_id = (container.labels or {}).get(LABEL_WORKSPACE_ID, "")
                if WORKSPACE_ID_RE.fullmatch(workspace_id):
                    try:
                        await asyncio.to_thread(_destroy_sync, workspace_id)
                    except DockerException:
                        pass


@asynccontextmanager
async def lifespan(_: FastAPI):
    if len(BROKER_TOKEN) < 32:
        raise RuntimeError("WORKSPACE_BROKER_TOKEN must contain at least 32 characters")
    try:
        docker_client.ping()
        docker_client.images.get(RUNTIME_IMAGE)
        _ensure_egress_network()
        _recover_workspace_networks()
    except DockerException as exc:
        raise RuntimeError("Workspace broker cannot initialize Docker control") from exc
    janitor = asyncio.create_task(_janitor())
    try:
        yield
    finally:
        janitor.cancel()
        try:
            await janitor
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="Inzozi Code Workspace Broker",
    version="0.1.0",
    description="Narrow privileged Docker broker for fixed workspace lifecycle operations",
    lifespan=lifespan,
)


@app.get("/health")
def health() -> dict:
    try:
        docker_client.ping()
        docker_client.images.get(RUNTIME_IMAGE)
        _ensure_egress_network()
    except (DockerException, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail="Workspace broker is not ready") from exc
    return {"status": "ok", "service": "workspace-broker", "docker_control": "broker-only"}


@app.get("/v1/isolation/status")
def isolation_status(request: Request) -> dict:
    _require_manager(request)
    return _isolation_status_sync()


@app.post("/v1/workspaces", status_code=201)
def create_workspace(payload: CreateWorkspaceRequest, request: Request) -> dict:
    _require_manager(request)
    try:
        return _create_workspace_sync(payload)
    except HTTPException:
        raise
    except (DockerException, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail="Workspace broker failed closed during provisioning") from exc


@app.post("/v1/workspaces/{workspace_id}/git/push")
def git_push(workspace_id: str, payload: PushRequest, request: Request) -> dict:
    _require_manager(request)
    try:
        _workspace_id(workspace_id)
        return _push_sync(workspace_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid workspace id") from exc
    except HTTPException:
        raise
    except (DockerException, RuntimeError) as exc:
        raise HTTPException(status_code=502, detail="Workspace broker push helper failed safely") from exc


@app.post("/v1/workspaces/{workspace_id}/quota/enforce")
def enforce_quota(workspace_id: str, request: Request) -> dict:
    _require_manager(request)
    try:
        _workspace_id(workspace_id)
        return _quota_sync(workspace_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid workspace id") from exc


@app.delete("/v1/workspaces/{workspace_id}", status_code=204)
def destroy_workspace(workspace_id: str, request: Request) -> None:
    _require_manager(request)
    try:
        _workspace_id(workspace_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid workspace id") from exc
    _destroy_sync(workspace_id)
