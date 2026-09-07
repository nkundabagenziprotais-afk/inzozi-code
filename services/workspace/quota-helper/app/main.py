from __future__ import annotations

import ctypes
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
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

# Linux/XFS UAPI constants from linux/dqblk_xfs.h and sys/quota.h.
XQM_PRJQUOTA = 2
Q_XGETQUOTA = (ord("X") << 8) + 3
Q_XSETQLIM = (ord("X") << 8) + 4
FS_DQUOT_VERSION = 1
FS_PROJ_QUOTA = 1 << 1
FS_DQ_BSOFT = 1 << 2
FS_DQ_BHARD = 1 << 3
FS_DQ_BLOCK_LIMITS = FS_DQ_BSOFT | FS_DQ_BHARD
BASIC_BLOCK_BYTES = 512

# Staging is amd64. x86_64 uses the asm-generic quotactl_fd syscall number.
SYS_QUOTACTL_FD_X86_64 = 443


def _qcmd(command: int, quota_type: int) -> int:
    return (command << 8) | (quota_type & 0xFF)


Q_XGETQUOTA_PROJECT = _qcmd(Q_XGETQUOTA, XQM_PRJQUOTA)
Q_XSETQLIM_PROJECT = _qcmd(Q_XSETQLIM, XQM_PRJQUOTA)


class FsDiskQuota(ctypes.Structure):
    """ABI-compatible fs_disk_quota from linux/dqblk_xfs.h."""

    _fields_ = [
        ("d_version", ctypes.c_int8),
        ("d_flags", ctypes.c_int8),
        ("d_fieldmask", ctypes.c_uint16),
        ("d_id", ctypes.c_uint32),
        ("d_blk_hardlimit", ctypes.c_uint64),
        ("d_blk_softlimit", ctypes.c_uint64),
        ("d_ino_hardlimit", ctypes.c_uint64),
        ("d_ino_softlimit", ctypes.c_uint64),
        ("d_bcount", ctypes.c_uint64),
        ("d_icount", ctypes.c_uint64),
        ("d_itimer", ctypes.c_int32),
        ("d_btimer", ctypes.c_int32),
        ("d_iwarns", ctypes.c_uint16),
        ("d_bwarns", ctypes.c_uint16),
        ("d_itimer_hi", ctypes.c_int8),
        ("d_btimer_hi", ctypes.c_int8),
        ("d_rtbtimer_hi", ctypes.c_int8),
        ("d_padding2", ctypes.c_int8),
        ("d_rtb_hardlimit", ctypes.c_uint64),
        ("d_rtb_softlimit", ctypes.c_uint64),
        ("d_rtbcount", ctypes.c_uint64),
        ("d_rtbtimer", ctypes.c_int32),
        ("d_rtbwarns", ctypes.c_uint16),
        ("d_padding3", ctypes.c_int16),
        ("d_padding4", ctypes.c_char * 8),
    ]


_LIBC = ctypes.CDLL(None, use_errno=True)
_SYSCALL = _LIBC.syscall
_SYSCALL.restype = ctypes.c_long


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


def _run(command: list[str], error_message: str, *, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        if detail:
            raise RuntimeError(f"{error_message}: {detail}")
        raise RuntimeError(error_message)
    return result


def _parse_project_id(output: str) -> int:
    match = re.search(r"projid\s*=\s*(\d+)", output)
    if not match:
        raise RuntimeError("Unable to verify XFS project id")
    return int(match.group(1))


def _project_inherit_enabled(output: str) -> bool:
    for line in output.splitlines():
        fields = line.split(maxsplit=1)
        if fields and "P" in fields[0]:
            return True
    return False


def _set_project(path: Path, project_id: int) -> None:
    _run(
        ["xfs_io", "-c", f"chproj {project_id}", str(path)],
        "Unable to assign XFS project id",
    )
    _run(
        ["xfs_io", "-c", "chattr +P", str(path)],
        "Unable to enable XFS project inheritance",
    )
    project = _run(
        ["xfs_io", "-c", "lsproj", str(path)],
        "Unable to verify XFS project id",
    )
    if _parse_project_id(project.stdout) != project_id:
        raise RuntimeError("XFS project id verification failed")
    attrs = _run(
        ["xfs_io", "-c", "lsattr", str(path)],
        "Unable to verify XFS project inheritance",
    )
    if not _project_inherit_enabled(attrs.stdout):
        raise RuntimeError("XFS project inheritance verification failed")


def _quota_basic_blocks(limit_bytes: int) -> int:
    return (limit_bytes + BASIC_BLOCK_BYTES - 1) // BASIC_BLOCK_BYTES


def _quota_record(project_id: int, limit_bytes: int) -> FsDiskQuota:
    record = FsDiskQuota()
    record.d_version = FS_DQUOT_VERSION
    record.d_flags = FS_PROJ_QUOTA
    record.d_fieldmask = FS_DQ_BLOCK_LIMITS
    record.d_id = project_id
    blocks = _quota_basic_blocks(limit_bytes)
    record.d_blk_softlimit = blocks
    record.d_blk_hardlimit = blocks
    return record


def _require_quotactl_fd_arch() -> None:
    if platform.machine().lower() not in {"x86_64", "amd64"}:
        raise RuntimeError("Workspace quota helper supports quotactl_fd on amd64 staging only")


def _quotactl_fd(operation: int, project_id: int, record: FsDiskQuota) -> None:
    _require_quotactl_fd_arch()
    flags = getattr(os, "O_PATH", os.O_RDONLY) | getattr(os, "O_DIRECTORY", 0)
    fd = os.open(ROOT, flags)
    try:
        ctypes.set_errno(0)
        result = _SYSCALL(
            SYS_QUOTACTL_FD_X86_64,
            fd,
            operation,
            project_id,
            ctypes.byref(record),
        )
        if result != 0:
            error_number = ctypes.get_errno()
            error_name = errno.errorcode.get(error_number, "UNKNOWN")
            raise RuntimeError(
                f"XFS project quota syscall failed with errno {error_number} ({error_name})"
            )
    finally:
        os.close(fd)


def _read_limit(project_id: int) -> FsDiskQuota:
    record = FsDiskQuota()
    record.d_version = FS_DQUOT_VERSION
    record.d_flags = FS_PROJ_QUOTA
    record.d_id = project_id
    _quotactl_fd(Q_XGETQUOTA_PROJECT, project_id, record)
    return record


def _set_limit(project_id: int, limit_bytes: int) -> None:
    expected_blocks = _quota_basic_blocks(limit_bytes)
    record = _quota_record(project_id, limit_bytes)
    _quotactl_fd(Q_XSETQLIM_PROJECT, project_id, record)

    observed = _read_limit(project_id)
    if observed.d_id != project_id:
        raise RuntimeError("XFS project quota id read-back verification failed")
    if not (observed.d_flags & FS_PROJ_QUOTA):
        raise RuntimeError("XFS project quota type read-back verification failed")
    if observed.d_blk_hardlimit != expected_blocks or observed.d_blk_softlimit != expected_blocks:
        raise RuntimeError("XFS project quota hard-limit read-back verification failed")


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
    record = _quota_record(project_id, 0)
    try:
        _quotactl_fd(Q_XSETQLIM_PROJECT, project_id, record)
    except RuntimeError:
        # Cleanup is best effort here; the path and registry marker are still removed.
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
    _require_quotactl_fd_arch()
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
        try:
            _set_project(path, project_id)
            _set_limit(project_id, limit_bytes)
            marker.write_text(f"{project_id}\n", encoding="ascii")
            os.chmod(marker, 0o600)
        except Exception:
            _clear_limit(project_id)
            shutil.rmtree(path, ignore_errors=True)
            marker.unlink(missing_ok=True)
            raise
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    return {
        "status": "quota-ready",
        "workspace_id": workspace_id,
        "project_id": project_id,
        "limit_bytes": limit_bytes,
        "quota_api": "quotactl-fd-xfs",
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
    _require_quotactl_fd_arch()
    return {
        "status": "ok",
        "filesystem": status["filesystem"],
        "project_quota": True,
        "quota_api": "quotactl-fd-xfs",
        "quota_root": str(ROOT),
    }


def _probe() -> dict:
    _mount_status()
    workspace_id = secrets.token_hex(16)
    limit_bytes = 1024 * 1024
    _setup(workspace_id, limit_bytes)
    path = _workspace_path(workspace_id)
    probe_path = path / "quota-probe.bin"
    try:
        fd = os.open(probe_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            remaining = 2 * 1024 * 1024
            chunk = b"0" * 65536
            while remaining:
                written = os.write(fd, chunk[: min(len(chunk), remaining)])
                if written <= 0:
                    raise RuntimeError("XFS project hard quota probe write made no progress")
                remaining -= written
            os.fsync(fd)
        except OSError as exc:
            if exc.errno != PROJECT_QUOTA_FAILURE_ERRNO:
                raise RuntimeError(
                    f"XFS project hard quota probe failed with unexpected errno {exc.errno}"
                ) from exc
            return {
                "status": "ok",
                "hard_limit_enforced": True,
                "failure_errno": PROJECT_QUOTA_FAILURE_ERRNO_NAME,
                "probe_limit_bytes": limit_bytes,
                "quota_api": "quotactl-fd-xfs",
            }
        finally:
            os.close(fd)
        raise RuntimeError(
            f"XFS project hard quota probe did not fail with {PROJECT_QUOTA_FAILURE_ERRNO_NAME}"
        )
    finally:
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
