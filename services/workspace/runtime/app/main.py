from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, SecretStr

from app.policy import PolicyError, recipe_for, resolve_inside

WORKSPACE_ROOT = Path(os.getenv("WORKSPACE_ROOT", "/workspaces"))
MAX_FILE_BYTES = int(os.getenv("WORKSPACE_MAX_FILE_BYTES", "1048576"))
MAX_OUTPUT_BYTES = int(os.getenv("WORKSPACE_MAX_OUTPUT_BYTES", "65536"))
MAX_SEARCH_FILE_BYTES = int(os.getenv("WORKSPACE_MAX_SEARCH_FILE_BYTES", "524288"))
MAX_CHECKPOINT_BYTES = int(os.getenv("WORKSPACE_MAX_CHECKPOINT_BYTES", str(100 * 1024 * 1024)))
GITHUB_HTTPS_RE = re.compile(r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?$")
SAFE_PUSH_BRANCH_RE = re.compile(r"^(feature|fix|ui|hotfix|deploy)/[a-z0-9][a-z0-9._-]{2,80}$")
SEARCH_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "vendor", "dist", "build", ".next", ".nuxt", "coverage"}

app = FastAPI(title="Inzozi Code Workspace Runtime", version="0.1.5")


class CreateWorkspaceRequest(BaseModel):
    repository_url: str
    ref: str | None = None
    git_token: SecretStr | None = Field(default=None, repr=False)


class RunActionRequest(BaseModel):
    action: str
    cwd: str | None = None


class WriteFileRequest(BaseModel):
    content: str
    expected_sha256: str | None = None


class CreateCheckpointRequest(BaseModel):
    label: str | None = Field(default=None, max_length=120)


class PushRequest(BaseModel):
    branch: str
    expected_head: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    git_token: SecretStr = Field(repr=False)


def _workspace_path(workspace_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", workspace_id):
        raise HTTPException(status_code=400, detail="Invalid workspace id")
    path = WORKSPACE_ROOT / workspace_id
    if not path.is_dir():
        raise HTTPException(status_code=404, detail="Workspace not found")
    return path


def _repo_path(workspace_id: str) -> Path:
    path = _workspace_path(workspace_id) / "repo"
    if not path.is_dir():
        raise HTTPException(status_code=409, detail="Workspace repository is unavailable")
    return path


def _metadata_path(workspace_id: str) -> Path:
    return _workspace_path(workspace_id) / "workspace.json"


def _read_workspace_metadata(workspace_id: str) -> dict:
    path = _metadata_path(workspace_id)
    if not path.is_file():
        raise HTTPException(status_code=409, detail="Workspace metadata is unavailable")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=409, detail="Workspace metadata is invalid") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=409, detail="Workspace metadata is invalid")
    return payload


def _checkpoints_path(workspace_id: str) -> Path:
    path = _workspace_path(workspace_id) / "checkpoints"
    path.mkdir(exist_ok=True)
    return path


def _checkpoint_path(workspace_id: str, checkpoint_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", checkpoint_id):
        raise HTTPException(status_code=400, detail="Invalid checkpoint id")
    path = _checkpoints_path(workspace_id) / checkpoint_id
    if not path.is_dir():
        raise HTTPException(status_code=404, detail="Checkpoint not found")
    return path


def _audit(workspace_id: str, event: str, details: dict) -> None:
    root = WORKSPACE_ROOT / workspace_id
    record = {"at": datetime.now(timezone.utc).isoformat(), "event": event, "details": details}
    with (root / "audit.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _run(argv: tuple[str, ...] | list[str], cwd: Path, timeout: int, extra_env: dict[str, str] | None = None) -> dict:
    env = os.environ.copy()
    env.update({"GIT_TERMINAL_PROMPT": "0", "CI": "1"})
    if extra_env:
        env.update(extra_env)
    try:
        completed = subprocess.run(
            list(argv),
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or b"")[-MAX_OUTPUT_BYTES:].decode("utf-8", errors="replace")
        return {"exit_code": 124, "output": output, "timed_out": True}
    output = completed.stdout[-MAX_OUTPUT_BYTES:].decode("utf-8", errors="replace")
    return {"exit_code": completed.returncode, "output": output, "timed_out": False}


def _git_auth_environment(root: Path, token: str) -> tuple[dict[str, str], Path]:
    askpass = root / ".git-askpass.sh"
    askpass.write_text(
        "#!/bin/sh\n"
        "case \"$1\" in\n"
        "  *Username*) printf '%s\\n' \"$INZOI_GIT_USERNAME\" ;;\n"
        "  *) printf '%s\\n' \"$INZOI_GIT_TOKEN\" ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    askpass.chmod(0o700)
    return {
        "GIT_ASKPASS": str(askpass),
        "GIT_ASKPASS_REQUIRE": "force",
        "INZOI_GIT_USERNAME": "x-access-token",
        "INZOI_GIT_TOKEN": token,
    }, askpass


def _tree_size(root: Path) -> int:
    total = 0
    for current, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [name for name in dirs if name != ".git"]
        base = Path(current)
        for name in files:
            try:
                total += (base / name).lstat().st_size
            except OSError:
                continue
            if total > MAX_CHECKPOINT_BYTES:
                return total
    return total


def _copy_snapshot(source: Path, destination: Path) -> None:
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns(".git"), symlinks=True)


def _restore_snapshot(snapshot: Path, repo: Path) -> None:
    for child in repo.iterdir():
        if child.name == ".git":
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink(missing_ok=True)

    for child in snapshot.iterdir():
        target = repo / child.name
        if child.is_symlink():
            os.symlink(os.readlink(child), target)
        elif child.is_dir():
            shutil.copytree(child, target, symlinks=True)
        else:
            shutil.copy2(child, target, follow_symlinks=False)


@app.get("/health")
def health() -> dict:
    WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
    return {"status": "ok", "service": "workspace-runtime", "mode": "guarded-recipes-checkpoints-push"}


@app.post("/v1/workspaces", status_code=201)
def create_workspace(request: CreateWorkspaceRequest) -> dict:
    if not GITHUB_HTTPS_RE.fullmatch(request.repository_url):
        raise HTTPException(status_code=400, detail="This milestone accepts GitHub HTTPS repository URLs only")
    WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
    workspace_id = uuid.uuid4().hex
    root = WORKSPACE_ROOT / workspace_id
    repo = root / "repo"
    root.mkdir(mode=0o700)
    clone = ["git", "-c", "credential.helper=", "clone", "--depth", "1"]
    if request.ref:
        clone += ["--branch", request.ref]
    clone += [request.repository_url, str(repo)]

    authenticated = request.git_token is not None
    auth_env: dict[str, str] | None = None
    askpass: Path | None = None
    if request.git_token is not None:
        auth_env, askpass = _git_auth_environment(root, request.git_token.get_secret_value())
    try:
        result = _run(clone, root, 180, extra_env=auth_env)
    finally:
        if askpass is not None:
            askpass.unlink(missing_ok=True)

    _audit(
        workspace_id,
        "workspace.clone",
        {"repository_url": request.repository_url, "ref": request.ref, "authenticated": authenticated, **result},
    )
    if result["exit_code"] != 0:
        shutil.rmtree(root, ignore_errors=True)
        detail = "Repository clone failed"
        if authenticated:
            detail += ". Confirm the GitHub App installation has access to this repository."
        raise HTTPException(status_code=502, detail=detail)

    metadata = {
        "repository_url": request.repository_url,
        "ref": request.ref,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    (root / "workspace.json").write_text(json.dumps(metadata), encoding="utf-8")
    return {"workspace_id": workspace_id, "repository_url": request.repository_url, "ref": request.ref, "status": "ready"}


@app.get("/v1/workspaces/{workspace_id}/metadata")
def workspace_metadata(workspace_id: str) -> dict:
    return _read_workspace_metadata(workspace_id)


@app.get("/v1/workspaces/{workspace_id}/tree")
def tree(workspace_id: str, path: str = "", limit: int = 300) -> dict:
    repo = _repo_path(workspace_id)
    try:
        target = resolve_inside(repo, path)
    except PolicyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not target.is_dir():
        raise HTTPException(status_code=404, detail="Directory not found")
    entries = []
    for item in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))[: min(limit, 500)]:
        if item.name == ".git":
            continue
        entries.append({"name": item.name, "type": "directory" if item.is_dir() else "file"})
    return {"path": path, "entries": entries}


@app.get("/v1/workspaces/{workspace_id}/search")
def search_workspace(workspace_id: str, q: str, limit: int = 80) -> dict:
    query = q.strip()
    if len(query) < 2 or len(query) > 120:
        raise HTTPException(status_code=400, detail="Search query must contain 2 to 120 characters")
    repo = _repo_path(workspace_id)
    folded = query.casefold()
    results: list[dict] = []
    max_results = min(max(limit, 1), 120)

    for current, dirs, files in os.walk(repo, followlinks=False):
        dirs[:] = [name for name in dirs if name not in SEARCH_SKIP_DIRS]
        base = Path(current)
        for name in files:
            if len(results) >= max_results:
                break
            path = base / name
            if path.is_symlink():
                continue
            try:
                if path.stat().st_size > MAX_SEARCH_FILE_BYTES:
                    continue
                raw = path.read_bytes()
            except OSError:
                continue
            if b"\x00" in raw:
                continue
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                continue
            relative = path.relative_to(repo).as_posix()
            for line_number, line in enumerate(text.splitlines(), start=1):
                if folded in line.casefold():
                    results.append({"path": relative, "line": line_number, "excerpt": line.strip()[:240]})
                    if len(results) >= max_results:
                        break
        if len(results) >= max_results:
            break

    return {"query": query, "results": results, "truncated": len(results) >= max_results}


@app.get("/v1/workspaces/{workspace_id}/files/{file_path:path}")
def read_file(workspace_id: str, file_path: str) -> dict:
    repo = _repo_path(workspace_id)
    try:
        target = resolve_inside(repo, file_path)
    except PolicyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    if target.stat().st_size > MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds workspace read limit")
    raw = target.read_bytes()
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=415, detail="Binary files are not readable in this milestone") from exc
    return {"path": file_path, "content": content, "sha256": hashlib.sha256(raw).hexdigest()}


@app.put("/v1/workspaces/{workspace_id}/files/{file_path:path}")
def write_file(workspace_id: str, file_path: str, request: WriteFileRequest) -> dict:
    repo = _repo_path(workspace_id)
    try:
        target = resolve_inside(repo, file_path)
    except PolicyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if ".git" in target.parts:
        raise HTTPException(status_code=403, detail="Git metadata is not writable")
    encoded = request.content.encode("utf-8")
    if len(encoded) > MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds workspace write limit")
    if target.exists() and request.expected_sha256:
        current = hashlib.sha256(target.read_bytes()).hexdigest()
        if current != request.expected_sha256:
            raise HTTPException(status_code=409, detail="File changed since it was read")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(encoded)
    digest = hashlib.sha256(encoded).hexdigest()
    _audit(workspace_id, "file.write", {"path": file_path, "sha256": digest, "bytes": len(encoded)})
    return {"path": file_path, "sha256": digest, "bytes": len(encoded)}


@app.post("/v1/workspaces/{workspace_id}/actions")
def run_action(workspace_id: str, request: RunActionRequest) -> dict:
    repo = _repo_path(workspace_id)
    try:
        cwd = resolve_inside(repo, request.cwd)
        recipe = recipe_for(request.action)
    except PolicyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not cwd.is_dir():
        raise HTTPException(status_code=404, detail="Working directory not found")
    result = _run(recipe.argv, cwd, recipe.timeout_seconds)
    _audit(workspace_id, "command.run", {"action": request.action, "cwd": request.cwd or "", **result})
    return {"action": request.action, **result}


@app.get("/v1/workspaces/{workspace_id}/git/status")
def git_status(workspace_id: str) -> dict:
    return _run(("git", "status", "--short", "--branch"), _repo_path(workspace_id), 30)


@app.get("/v1/workspaces/{workspace_id}/git/diff")
def git_diff(workspace_id: str) -> dict:
    return _run(("git", "diff", "--no-ext-diff", "--minimal"), _repo_path(workspace_id), 30)


@app.post("/v1/workspaces/{workspace_id}/git/push")
def git_push(workspace_id: str, request: PushRequest) -> dict:
    if not SAFE_PUSH_BRANCH_RE.fullmatch(request.branch):
        raise HTTPException(status_code=400, detail="Remote push is limited to safe Inzozi Code branches")

    root = _workspace_path(workspace_id)
    repo = _repo_path(workspace_id)
    metadata = _read_workspace_metadata(workspace_id)
    repository_url = metadata.get("repository_url")
    if not isinstance(repository_url, str) or not GITHUB_HTTPS_RE.fullmatch(repository_url):
        raise HTTPException(status_code=409, detail="Workspace repository metadata is invalid")

    branch_result = _run(("git", "branch", "--show-current"), repo, 30)
    head_result = _run(("git", "rev-parse", "HEAD"), repo, 30)
    status_result = _run(("git", "status", "--porcelain"), repo, 30)
    branch = branch_result.get("output", "").strip()
    head = head_result.get("output", "").strip()
    status = status_result.get("output", "").strip()

    if branch != request.branch:
        raise HTTPException(status_code=409, detail="Current local branch differs from the approved push branch")
    if head != request.expected_head:
        raise HTTPException(status_code=409, detail="Current local commit differs from the approved push commit")
    if status:
        raise HTTPException(status_code=409, detail="Working tree must be clean before remote push")

    auth_env, askpass = _git_auth_environment(root, request.git_token.get_secret_value())
    try:
        result = _run(
            (
                "git",
                "-c", "credential.helper=",
                "push",
                "--porcelain",
                "origin",
                f"HEAD:refs/heads/{request.branch}",
            ),
            repo,
            120,
            extra_env=auth_env,
        )
    finally:
        askpass.unlink(missing_ok=True)

    _audit(
        workspace_id,
        "git.push",
        {
            "repository_url": repository_url,
            "branch": request.branch,
            "head": head,
            "exit_code": result.get("exit_code"),
            "timed_out": result.get("timed_out", False),
            "authenticated": True,
        },
    )
    if result.get("exit_code") != 0:
        raise HTTPException(status_code=502, detail="Git push failed. No force push was attempted.")
    return {
        "status": "pushed",
        "repository_url": repository_url,
        "branch": request.branch,
        "commit_sha": head,
        "forced": False,
    }


@app.post("/v1/workspaces/{workspace_id}/checkpoints", status_code=201)
def create_checkpoint(workspace_id: str, request: CreateCheckpointRequest) -> dict:
    repo = _repo_path(workspace_id)
    size_bytes = _tree_size(repo)
    if size_bytes > MAX_CHECKPOINT_BYTES:
        raise HTTPException(status_code=413, detail="Working tree is too large for a V0.1 checkpoint")

    checkpoint_id = uuid.uuid4().hex
    root = _checkpoints_path(workspace_id) / checkpoint_id
    snapshot = root / "repo"
    root.mkdir(mode=0o700)
    _copy_snapshot(repo, snapshot)
    created_at = datetime.now(timezone.utc).isoformat()
    status = _run(("git", "status", "--short", "--branch"), repo, 30)
    metadata = {
        "checkpoint_id": checkpoint_id,
        "label": request.label or "Manual checkpoint",
        "created_at": created_at,
        "size_bytes": size_bytes,
        "git_status": status.get("output", ""),
    }
    (root / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    _audit(workspace_id, "checkpoint.create", metadata)
    return metadata


@app.get("/v1/workspaces/{workspace_id}/checkpoints")
def list_checkpoints(workspace_id: str) -> dict:
    items = []
    for root in _checkpoints_path(workspace_id).iterdir():
        metadata = root / "metadata.json"
        if not root.is_dir() or not metadata.is_file():
            continue
        try:
            items.append(json.loads(metadata.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    items.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    return {"checkpoints": items[:20]}


@app.post("/v1/workspaces/{workspace_id}/checkpoints/{checkpoint_id}/restore")
def restore_checkpoint(workspace_id: str, checkpoint_id: str) -> dict:
    root = _checkpoint_path(workspace_id, checkpoint_id)
    snapshot = root / "repo"
    if not snapshot.is_dir():
        raise HTTPException(status_code=409, detail="Checkpoint snapshot is unavailable")
    repo = _repo_path(workspace_id)
    _restore_snapshot(snapshot, repo)
    _audit(workspace_id, "checkpoint.restore", {"checkpoint_id": checkpoint_id})
    return {"checkpoint_id": checkpoint_id, "status": "restored"}


@app.delete("/v1/workspaces/{workspace_id}/checkpoints/{checkpoint_id}", status_code=204)
def delete_checkpoint(workspace_id: str, checkpoint_id: str) -> None:
    root = _checkpoint_path(workspace_id, checkpoint_id)
    shutil.rmtree(root)
    _audit(workspace_id, "checkpoint.delete", {"checkpoint_id": checkpoint_id})


@app.delete("/v1/workspaces/{workspace_id}", status_code=204)
def destroy_workspace(workspace_id: str) -> None:
    shutil.rmtree(_workspace_path(workspace_id))
