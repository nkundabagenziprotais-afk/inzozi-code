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
from pydantic import BaseModel

from app.policy import PolicyError, recipe_for, resolve_inside

WORKSPACE_ROOT = Path(os.getenv("WORKSPACE_ROOT", "/workspaces"))
MAX_FILE_BYTES = int(os.getenv("WORKSPACE_MAX_FILE_BYTES", "1048576"))
MAX_OUTPUT_BYTES = int(os.getenv("WORKSPACE_MAX_OUTPUT_BYTES", "65536"))
GITHUB_HTTPS_RE = re.compile(r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?$")

app = FastAPI(title="Inzozi Code Workspace Runtime", version="0.1.1")


class CreateWorkspaceRequest(BaseModel):
    repository_url: str
    ref: str | None = None


class RunActionRequest(BaseModel):
    action: str
    cwd: str | None = None


class WriteFileRequest(BaseModel):
    content: str
    expected_sha256: str | None = None


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


def _audit(workspace_id: str, event: str, details: dict) -> None:
    root = WORKSPACE_ROOT / workspace_id
    record = {"at": datetime.now(timezone.utc).isoformat(), "event": event, "details": details}
    with (root / "audit.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _run(argv: tuple[str, ...] | list[str], cwd: Path, timeout: int) -> dict:
    env = os.environ.copy()
    env.update({"GIT_TERMINAL_PROMPT": "0", "CI": "1"})
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


@app.get("/health")
def health() -> dict:
    WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
    return {"status": "ok", "service": "workspace-runtime", "mode": "guarded-recipes"}


@app.post("/v1/workspaces", status_code=201)
def create_workspace(request: CreateWorkspaceRequest) -> dict:
    if not GITHUB_HTTPS_RE.fullmatch(request.repository_url):
        raise HTTPException(status_code=400, detail="V0.1.1 accepts GitHub HTTPS repository URLs only")
    WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
    workspace_id = uuid.uuid4().hex
    root = WORKSPACE_ROOT / workspace_id
    repo = root / "repo"
    root.mkdir(mode=0o700)
    clone = ["git", "clone", "--depth", "1"]
    if request.ref:
        clone += ["--branch", request.ref]
    clone += [request.repository_url, str(repo)]
    result = _run(clone, root, 180)
    _audit(workspace_id, "workspace.clone", {"repository_url": request.repository_url, "ref": request.ref, **result})
    if result["exit_code"] != 0:
        shutil.rmtree(root, ignore_errors=True)
        raise HTTPException(status_code=502, detail="Repository clone failed. Private repositories require the planned GitHub App installation-token flow.")
    return {"workspace_id": workspace_id, "repository_url": request.repository_url, "ref": request.ref, "status": "ready"}


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
        raise HTTPException(status_code=415, detail="Binary files are not readable in V0.1.1") from exc
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


@app.delete("/v1/workspaces/{workspace_id}", status_code=204)
def destroy_workspace(workspace_id: str) -> None:
    shutil.rmtree(_workspace_path(workspace_id))
