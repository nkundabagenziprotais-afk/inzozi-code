from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import hmac
import json
import os
import re
import socket
import time

import docker
from docker.errors import ContainerError, DockerException, NotFound
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field, SecretStr

from app.env_builders import (
    bootstrap_helper_environment,
    push_helper_environment,
    quota_helper_environment,
    runtime_container_environment,
)

RUNTIME_IMAGE = os.getenv("WORKSPACE_RUNTIME_IMAGE", "inzozi-code-workspace-runtime:local")
QUOTA_HELPER_IMAGE = os.getenv("WORKSPACE_QUOTA_HELPER_IMAGE", "inzozi-code-workspace-quota-helper:local")
QUOTA_STORAGE_ROOT = os.getenv("WORKSPACE_QUOTA_STORAGE_ROOT", "/srv/inzozi-code/workspace-data").rstrip("/")
MANAGER_SERVICE_HOST = os.getenv("WORKSPACE_MANAGER_SERVICE_HOST", "workspace-manager")
BROKER_TOKEN = os.getenv("WORKSPACE_BROKER_TOKEN", "")
EGRESS_NETWORK = "inzozi-workspace-helper-restricted"
EGRESS_UPLINK_NETWORK = "inzozi-workspace-egress-uplink"
EGRESS_PROXY_URL = "http://workspace-egress-proxy:3128"
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
LABEL_PROJECT_ID = "com.inzozi.code.project_id"


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


def _quota_host_path(workspace_id: str) -> str:
    workspace_id = _workspace_id(workspace_id)
    if not QUOTA_STORAGE_ROOT.startswith("/") or QUOTA_STORAGE_ROOT == "/":
        raise RuntimeError("Workspace quota storage root must be a dedicated absolute path")
    return f"{QUOTA_STORAGE_ROOT}/{workspace_id}"


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


def _workspace_labels(workspace_id: str, expires_at: int, network_name: str, project_id: int) -> dict[str, str]:
    return {
        LABEL_WORKSPACE: "true",
        LABEL_WORKSPACE_ID: workspace_id,
        LABEL_EXPIRES_AT: str(expires_at),
        LABEL_NETWORK: network_name,
        LABEL_KIND: "runtime",
        LABEL_PROJECT_ID: str(project_id),
    }


def _egress_proxy_container():
    project = _compose_project()
    labels = [f"{LABEL_ROLE}=workspace-egress-proxy"]

    if project:
        labels.append(f"com.docker.compose.project={project}")

    containers = docker_client.containers.list(
        all=True,
        filters={"label": labels},
    )

    if len(containers) != 1:
        raise RuntimeError(
            "Workspace GitHub egress proxy identity is unavailable"
        )

    return containers[0]


def _ensure_egress_network() -> None:
    try:
        helper_network = docker_client.networks.get(EGRESS_NETWORK)
        uplink_network = docker_client.networks.get(
            EGRESS_UPLINK_NETWORK
        )
    except NotFound as exc:
        raise RuntimeError(
            "Restricted workspace helper networks are unavailable"
        ) from exc

    helper_network.reload()
    uplink_network.reload()

    if not bool(helper_network.attrs.get("Internal")):
        raise RuntimeError(
            "Workspace helper egress network must be internal"
        )

    if bool(uplink_network.attrs.get("Internal")):
        raise RuntimeError(
            "Workspace proxy uplink must provide outbound connectivity"
        )

    proxy = _egress_proxy_container()

    helper_endpoints = (
        helper_network.attrs.get("Containers") or {}
    )
    uplink_endpoints = (
        uplink_network.attrs.get("Containers") or {}
    )

    if proxy.id not in helper_endpoints:
        raise RuntimeError(
            "Workspace GitHub egress proxy is not on helper network"
        )

    if proxy.id not in uplink_endpoints:
        raise RuntimeError(
            "Workspace GitHub egress proxy is not on uplink network"
        )

    for container_id in helper_endpoints:
        if container_id == proxy.id:
            continue

        try:
            endpoint = docker_client.containers.get(container_id)
        except NotFound:
            continue

        if (
            endpoint.labels or {}
        ).get(LABEL_KIND) != "workspace-helper":
            raise RuntimeError(
                "Unexpected container attached to helper egress"
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


def _quota_helper_environment(*, workspace_id: str | None = None, limit_bytes: int | None = None) -> dict[str, str]:
    resolved_id = _workspace_id(workspace_id) if workspace_id is not None else None
    return quota_helper_environment(workspace_id=resolved_id, limit_bytes=limit_bytes)


def _bootstrap_helper_environment(
    *,
    repository_url: str,
    ref: str,
    git_token: str,
) -> dict[str, str]:
    return bootstrap_helper_environment(
        repository_url=repository_url,
        ref=ref,
        git_token=git_token,
    )


def _runtime_container_environment() -> dict[str, str]:
    return runtime_container_environment(
        max_file_bytes=MAX_FILE_BYTES,
        max_output_bytes=MAX_OUTPUT_BYTES,
        max_search_file_bytes=MAX_SEARCH_FILE_BYTES,
        max_checkpoint_bytes=MAX_CHECKPOINT_BYTES,
    )


def _push_helper_environment(*, branch: str, expected_head: str, git_token: str) -> dict[str, str]:
    return push_helper_environment(branch=branch, expected_head=expected_head, git_token=git_token)


def _quota_helper_run(action: str, *, workspace_id: str | None = None, limit_bytes: int | None = None) -> dict:
    if action not in {"check", "setup", "destroy", "probe"}:
        raise RuntimeError("Unsupported quota helper action")
    environment = _quota_helper_environment(workspace_id=workspace_id, limit_bytes=limit_bytes)

    try:
        output = docker_client.containers.run(
            image=QUOTA_HELPER_IMAGE,
            command=[action],
            environment=environment,
            volumes={QUOTA_STORAGE_ROOT: {"bind": "/quota-root", "mode": "rw"}},
            network_mode="none",
            read_only=True,
            tmpfs={"/tmp": "rw,noexec,nosuid,nodev,size=67108864"},
            user="0:0",
            cap_drop=["ALL"],
            cap_add=["SYS_ADMIN", "CHOWN"],
            security_opt=["no-new-privileges:true"],
            mem_limit="256m",
            nano_cpus=500_000_000,
            pids_limit=64,
            remove=True,
            stdout=True,
            stderr=True,
            labels={LABEL_KIND: "workspace-quota-helper"},
        )
    except ContainerError as exc:
        raise RuntimeError("Workspace XFS quota helper failed safely") from exc

    try:
        payload = json.loads(output.decode("utf-8").strip().splitlines()[-1])
    except (ValueError, IndexError, json.JSONDecodeError) as exc:
        raise RuntimeError("Workspace XFS quota helper returned an invalid result") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Workspace XFS quota helper returned an invalid result")
    return payload


def _destroy_sync(workspace_id: str) -> None:
    workspace_id = _workspace_id(workspace_id)
    errors: list[Exception] = []

    try:
        docker_client.containers.get(_runtime_name(workspace_id)).remove(force=True)
    except NotFound:
        pass
    except DockerException as exc:
        errors.append(exc)

    try:
        network = docker_client.networks.get(_network_name(workspace_id))
        try:
            network.disconnect(_manager_container(), force=True)
        except (DockerException, RuntimeError):
            pass
        network.remove()
    except NotFound:
        pass
    except DockerException as exc:
        errors.append(exc)

    try:
        docker_client.volumes.get(_volume_name(workspace_id)).remove(force=True)
    except NotFound:
        pass
    except DockerException as exc:
        errors.append(exc)

    try:
        _quota_helper_run("destroy", workspace_id=workspace_id)
    except (DockerException, RuntimeError) as exc:
        errors.append(exc)

    if errors:
        raise RuntimeError("Workspace cleanup did not complete safely") from errors[0]


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
        raise RuntimeError(
            "Unsupported workspace helper module"
        )

    if network_name not in {None, EGRESS_NETWORK}:
        raise RuntimeError(
            "Unsupported workspace helper network"
        )

    helper_environment = dict(environment)

    if network_name == EGRESS_NETWORK:
        helper_environment.update(
            {
                "HTTPS_PROXY": EGRESS_PROXY_URL,
                "https_proxy": EGRESS_PROXY_URL,
                "HTTP_PROXY": EGRESS_PROXY_URL,
                "http_proxy": EGRESS_PROXY_URL,
                "NO_PROXY": "",
                "no_proxy": "",
            }
        )

    kwargs = {
        "image": RUNTIME_IMAGE,
        "command": ["python", "-m", module],
        "environment": helper_environment,
        "volumes": {
            volume_name: {
                "bind": "/workspace",
                "mode": "rw",
            }
        },
        "read_only": True,
        "tmpfs": {
            "/tmp": (
                "rw,noexec,nosuid,nodev,"
                "size=134217728"
            )
        },
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "mem_limit": "1g",
        "nano_cpus": 1_000_000_000,
        "pids_limit": 128,
        "remove": True,
        "stdout": True,
        "stderr": True,
        "labels": {
            LABEL_KIND: "workspace-helper",
        },
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
        raise RuntimeError(
            "Workspace helper action failed safely"
        ) from exc


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

    quota = _quota_helper_run("setup", workspace_id=workspace_id, limit_bytes=DISK_LIMIT_BYTES)
    try:
        project_id = int(quota.get("project_id", 0))
    except (TypeError, ValueError) as exc:
        _quota_helper_run("destroy", workspace_id=workspace_id)
        raise RuntimeError("Workspace quota helper did not allocate a valid project id") from exc
    if project_id <= 0 or quota.get("status") != "quota-ready":
        _quota_helper_run("destroy", workspace_id=workspace_id)
        raise RuntimeError("Workspace quota helper did not allocate a valid project id")

    try:
        docker_client.volumes.create(
            name=volume_name,
            driver="local",
            driver_opts={"type": "none", "o": "bind", "device": _quota_host_path(workspace_id)},
            labels={
                LABEL_WORKSPACE: "true",
                LABEL_WORKSPACE_ID: workspace_id,
                LABEL_KIND: "volume",
                LABEL_PROJECT_ID: str(project_id),
            },
        )
        network = docker_client.networks.create(
            network_name,
            driver="bridge",
            internal=True,
            labels={LABEL_WORKSPACE: "true", LABEL_WORKSPACE_ID: workspace_id, LABEL_KIND: "runtime-network"},
        )

        _connect_manager_to(network)
        _helper_run(
            module="app.bootstrap",
            environment=_bootstrap_helper_environment(
                repository_url=payload.repository_url,
                ref=payload.ref or "",
                git_token=payload.git_token.get_secret_value() if payload.git_token else "",
            ),
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
            environment=_runtime_container_environment(),
            read_only=True,
            tmpfs={"/tmp": RUNTIME_TMPFS},
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            mem_limit=RUNTIME_MEMORY,
            nano_cpus=RUNTIME_NANO_CPUS,
            pids_limit=RUNTIME_PIDS_LIMIT,
            restart_policy={"Name": "unless-stopped"},
            labels=_workspace_labels(workspace_id, payload.expires_at, network_name, project_id),
        )
    except Exception:
        try:
            _destroy_sync(workspace_id)
        except RuntimeError:
            pass
        raise

    return {
        "workspace_id": workspace_id,
        "status": "provisioned",
        "size_bytes": size_bytes,
        "network_internal": True,
        "disk_quota": "xfs-project-hard",
        "disk_limit_bytes": DISK_LIMIT_BYTES,
        "project_id": project_id,
    }


def _push_sync(workspace_id: str, request: PushRequest) -> dict:
    _assert_live_sync(workspace_id)
    if not SAFE_PUSH_BRANCH_RE.fullmatch(request.branch):
        raise HTTPException(status_code=400, detail="Remote push is limited to safe Inzozi Code branches")
    if not HEAD_RE.fullmatch(request.expected_head):
        raise HTTPException(status_code=400, detail="Invalid reviewed commit")
    output = _helper_run(
        module="app.remote_push",
        environment=_push_helper_environment(
            branch=request.branch,
            expected_head=request.expected_head,
            git_token=request.git_token.get_secret_value(),
        ),
        volume_name=_volume_name(workspace_id),
        network_name=EGRESS_NETWORK,
    )

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
        "hard_enforced": True,
        "mode": "xfs-project-hard",
        "exceeded": exceeded,
        "cleaned_up": exceeded,
    }


def _isolation_status_sync() -> dict:
    quota_status = _quota_helper_run("check")
    containers = _active_containers()
    workspaces: list[dict] = []
    socket_mounts = 0
    network_names: set[str] = set()
    project_ids: set[str] = set()
    all_networks_internal = True
    all_hard_quotas = True
    for container in containers:
        container.reload()
        labels = container.labels or {}
        workspace_id = labels.get(LABEL_WORKSPACE_ID, "")
        network_name = labels.get(LABEL_NETWORK, "")
        project_id = labels.get(LABEL_PROJECT_ID, "")
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
        quota_bound = project_id.isdigit() and int(project_id) > 0
        all_hard_quotas = all_hard_quotas and quota_bound
        if quota_bound:
            project_ids.add(project_id)
        workspaces.append(
            {
                "workspace_id": workspace_id,
                "status": container.status,
                "network": network_name,
                "network_internal": network_internal,
                "expires_at": labels.get(LABEL_EXPIRES_AT),
                "disk_quota": "xfs-project-hard" if quota_bound else "missing",
                "project_id": project_id or None,
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
        "git_helper_egress": "github-only-connect-proxy",
        "disk_quota_bytes": DISK_LIMIT_BYTES,
        "disk_quota_mode": "xfs-project-hard",
        "quota_filesystem": quota_status.get("filesystem"),
        "quota_project_enforcement": quota_status.get("project_quota") is True,
        "all_active_workspaces_hard_quoted": all_hard_quotas,
        "unique_active_project_ids": len(project_ids) == len(workspaces),
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
                    except (DockerException, RuntimeError):
                        pass


@asynccontextmanager
async def lifespan(_: FastAPI):
    if len(BROKER_TOKEN) < 32:
        raise RuntimeError("WORKSPACE_BROKER_TOKEN must contain at least 32 characters")
    try:
        docker_client.ping()
        docker_client.images.get(RUNTIME_IMAGE)
        docker_client.images.get(QUOTA_HELPER_IMAGE)
        _ensure_egress_network()
        _quota_helper_run("check")
        _recover_workspace_networks()
    except (DockerException, RuntimeError) as exc:
        raise RuntimeError("Workspace broker cannot initialize hardened Docker/quota control") from exc
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
    version="0.2.0",
    description="Narrow privileged Docker broker with XFS project quotas for fixed workspace lifecycle operations",
    lifespan=lifespan,
)


@app.get("/health")
def health() -> dict:
    try:
        docker_client.ping()
        docker_client.images.get(RUNTIME_IMAGE)
        docker_client.images.get(QUOTA_HELPER_IMAGE)
        _ensure_egress_network()
    except (DockerException, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail="Workspace broker is not ready") from exc
    return {
        "status": "ok",
        "service": "workspace-broker",
        "docker_control": "broker-only",
        "disk_quota": "xfs-project-hard",
    }


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
        _destroy_sync(workspace_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid workspace id") from exc
    except (DockerException, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail="Workspace cleanup failed closed") from exc
