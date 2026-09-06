from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys

ROOT = Path("/quota-root")
REGISTRY = ROOT / ".inzozi-projects"
LOCK_FILE = REGISTRY / ".lock"
WORKSPACE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
MIN_PROJECT_ID = 10_000
MAX_PROJECT_ID = 2_000_000_000
PROJECT_QUOTA_FAILURE_ERRNO = errno.ENOSPC
PROJECT_QUOTA_FAILURE_ERRNO_NAME = "ENOSPC"


def _emit(payload: dict) -> None:
    print(json.dumps(payload, separators=(",", ":"), sort_keys=True))


def _workspace_id() -> str:
    value = os.getenv("WORKSPACE_ID", "").strip()
    if not WORKSPACE_ID_RE.fullmatch(value):
        raise RuntimeError("WORKSPACE_ID must be a 32-character lowercase hex identifier")
    return value


def _limit_bytes() -> int:
    raw = os.getenv("WORKSPACE_DISK_LIMIT_BYTES", "").strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError("WORKSPACE_DISK_LIMIT_BYTES must be an integer") from exc
    if value < 1024 * 1024:
        raise RuntimeError("Workspace disk quota must be at least 1 MiB")
    return value


def _workspace_path(workspace_id: str) -> Path:
    return ROOT / workspace_id


def _registry_path(workspace_id: str) -> Path:
    return REGISTRY / workspace_id


def _mount_status() -> dict[str, str]:
    if not ROOT.exists() or not ROOT.is_dir():
        raise RuntimeError("Quota storage root is unavailable")
    result = subprocess.run(
        ["findmnt", "-n", "-T", str(ROOT), "-o", "FSTYPE,OPTIONS"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        raise RuntimeError("Unable to inspect quota storage filesystem")
    fields = result.stdout.strip().split(maxsplit=1)
    if len(fields) != 2:
        raise RuntimeError("Quota storage filesystem status is malformed")
    filesystem, options = fields
    option_set = set(options.split(","))
    if filesystem != "xfs":
        raise RuntimeError("Workspace quota storage must use XFS")
    if not ({"prjquota", "pquota"} & option_set):
        raise RuntimeError("Workspace quota storage must be mounted with project quota enforcement")
    return {"filesystem": filesystem, "options": options}


def _run_xfs(command: str) -> None:
    result = subprocess.run(
        ["xfs_quota", "-x", "-c", command, str(ROOT)],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError("XFS project quota operation failed")


def _candidate_project_ids(workspace_id: str):
    for counter in range(64):
        digest = hashlib.sha256(f"{workspace_id}:{counter}".encode("ascii")).digest()
        value = int.from_bytes(digest[:4], "big")
        yield MIN_PROJECT_ID + (value % (MAX_PROJECT_ID - MIN_PROJECT_ID))


def _active_project_ids() -> set[int]:
    active: set[int] = set()
    if not REGISTRY.exists():
        return active
    for marker in REGISTRY.iterdir():
        if not WORKSPACE_ID_RE.fullmatch(marker.name) or not marker.is_file():
            continue
        try:
            active.add(int(marker.read_text(encoding="ascii").strip()))
        except (OSError, ValueError):
            raise RuntimeError("Workspace project quota registry is corrupt")
    return active


def _allocate_project_id(workspace_id: str) -> int:
    active = _active_project_ids()
    for candidate in _candidate_project_ids(workspace_id):
        if candidate not in active:
            return candidate
    raise RuntimeError("Unable to allocate a unique XFS project quota id")


def _clear_limit(project_id: int) -> None:
    try:
        _run_xfs(f"limit -p bsoft=0 bhard=0 {project_id}")
    except RuntimeError:
        pass


def _reclaim_tree_for_removal(path: Path) -> None:
    """Reclaim directory traversal without granting DAC_OVERRIDE or following symlinks."""
    os.chown(path, 0, 0, follow_symlinks=False)
    os.chmod(path, 0o700, follow_symlinks=False)
    for current, dirs, _files in os.walk(path, topdown=True, followlinks=False):
        base = Path(current)
        for name in dirs:
            child = base / name
            if child.is_symlink():
                continue
            os.chown(child, 0, 0, follow_symlinks=False)
            os.chmod(child, 0o700, follow_symlinks=False)


def _setup(workspace_id: str, limit_bytes: int) -> dict:
    _mount_status()
    REGISTRY.mkdir(mode=0o700, parents=True, exist_ok=True)
    LOCK_FILE.touch(mode=0o600, exist_ok=True)

    with LOCK_FILE.open("r+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        marker = _registry_path(workspace_id)
        path = _workspace_path(workspace_id)
        if marker.exists() or path.exists():
            raise RuntimeError("Workspace quota resource already exists")

        project_id = _allocate_project_id(workspace_id)
        path.mkdir(mode=0o700)
        limit_kib = (limit_bytes + 1023) // 1024
        try:
            _run_xfs(f"project -s -p {path} {project_id}")
            _run_xfs(f"limit -p bsoft={limit_kib}k bhard={limit_kib}k {project_id}")
            marker.write_text(f"{project_id}\n", encoding="ascii")
            os.chmod(marker, 0o600)
        except Exception:
            shutil.rmtree(path, ignore_errors=True)
            _clear_limit(project_id)
            marker.unlink(missing_ok=True)
            raise
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    return {
        "status": "quota-ready",
        "workspace_id": workspace_id,
        "project_id": project_id,
        "limit_bytes": limit_bytes,
    }


def _destroy(workspace_id: str) -> dict:
    _mount_status()
    REGISTRY.mkdir(mode=0o700, parents=True, exist_ok=True)
    LOCK_FILE.touch(mode=0o600, exist_ok=True)

    with LOCK_FILE.open("r+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        marker = _registry_path(workspace_id)
        path = _workspace_path(workspace_id)
        project_id: int | None = None
        if marker.exists():
            try:
                project_id = int(marker.read_text(encoding="ascii").strip())
            except (OSError, ValueError) as exc:
                raise RuntimeError("Workspace project quota registry is corrupt") from exc

        if path.is_symlink():
            raise RuntimeError("Refusing to remove a symlinked workspace quota path")
        if path.exists():
            _reclaim_tree_for_removal(path)
            shutil.rmtree(path)
        if project_id is not None:
            _clear_limit(project_id)
        marker.unlink(missing_ok=True)
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    return {"status": "quota-removed", "workspace_id": workspace_id}


def _check() -> dict:
    status = _mount_status()
    return {
        "status": "ok",
        "filesystem": status["filesystem"],
        "project_quota": True,
        "quota_root": str(ROOT),
    }


def _probe() -> dict:
    _mount_status()
    workspace_id = secrets.token_hex(16)
    limit_bytes = 1024 * 1024
    original_root_mode = ROOT.stat().st_mode & 0o7777
    _setup(workspace_id, limit_bytes)
    path = _workspace_path(workspace_id)
    try:
        os.chown(path, 65534, 65534)
        # The probe writer needs traverse-only access to the quota root. Preserve the
        # root-only registry (0700) and restore the mount-point mode immediately after.
        os.chmod(ROOT, original_root_mode | 0o001)
        script = (
            "import errno, os, sys\n"
            f"path={str(path / 'quota-probe.bin')!r}\n"
            "try:\n"
            "    with open(path, 'wb') as handle:\n"
            "        handle.write(b'0' * (2 * 1024 * 1024))\n"
            "        handle.flush()\n"
            "        os.fsync(handle.fileno())\n"
            "except OSError as exc:\n"
            f"    raise SystemExit(0 if exc.errno == {PROJECT_QUOTA_FAILURE_ERRNO} else 3)\n"
            "raise SystemExit(2)\n"
        )
        result = subprocess.run(
            [
                "setpriv",
                "--reuid=65534",
                "--regid=65534",
                "--clear-groups",
                "python",
                "-c",
                script,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"XFS project hard quota probe did not fail with {PROJECT_QUOTA_FAILURE_ERRNO_NAME}"
            )
        return {
            "status": "ok",
            "hard_limit_enforced": True,
            "failure_errno": PROJECT_QUOTA_FAILURE_ERRNO_NAME,
            "probe_limit_bytes": limit_bytes,
        }
    finally:
        os.chmod(ROOT, original_root_mode)
        _destroy(workspace_id)


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in {"check", "setup", "destroy", "probe"}:
        print("Unsupported quota helper action", file=sys.stderr)
        return 2

    action = sys.argv[1]
    try:
        if action == "check":
            payload = _check()
        elif action == "probe":
            payload = _probe()
        elif action == "setup":
            payload = _setup(_workspace_id(), _limit_bytes())
        else:
            payload = _destroy(_workspace_id())
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 3

    _emit(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
