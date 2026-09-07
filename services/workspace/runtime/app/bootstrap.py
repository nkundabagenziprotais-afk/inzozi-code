from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from app.git_credentials import github_git_environment

GITHUB_HTTPS_RE = re.compile(r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?$")
REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,159}$")
ROOT = Path("/workspace")
REPO = ROOT / "repo"


def _raise_walk_error(exc: OSError) -> None:
    raise RuntimeError("Workspace ownership traversal failed") from exc


def _set_owner(path: Path, uid: int, gid: int) -> None:
    os.chown(path, uid, gid, follow_symlinks=False)
    stat = path.lstat()
    if stat.st_uid != uid or stat.st_gid != gid:
        raise RuntimeError("Workspace ownership verification failed")


def _chown_tree(root: Path, uid: int, gid: int) -> None:
    if root.is_symlink() or not root.is_dir():
        raise RuntimeError("Workspace root is unavailable or symlinked")

    # The bootstrap helper deliberately has CAP_CHOWN but not DAC_OVERRIDE.
    # The quota-backed root starts as root:root mode 0700, so changing the
    # root owner before walking descendants makes the helper lose traversal.
    # Walk bottom-up and transfer the root itself last.
    for current, dirs, files in os.walk(
        root,
        topdown=False,
        followlinks=False,
        onerror=_raise_walk_error,
    ):
        base = Path(current)
        for name in files:
            path = base / name
            try:
                _set_owner(path, uid, gid)
            except FileNotFoundError:
                continue
        for name in dirs:
            path = base / name
            try:
                _set_owner(path, uid, gid)
            except FileNotFoundError:
                continue

    _set_owner(root, uid, gid)


def main() -> int:
    repository_url = os.getenv("WORKSPACE_REPOSITORY_URL", "").strip()
    ref = os.getenv("WORKSPACE_REF", "").strip()
    token = os.getenv("WORKSPACE_GIT_TOKEN", "")
    uid = int(os.getenv("WORKSPACE_OWNER_UID", "10002"))
    gid = int(os.getenv("WORKSPACE_OWNER_GID", "10002"))

    if not GITHUB_HTTPS_RE.fullmatch(repository_url):
        print("Invalid GitHub repository URL", file=sys.stderr)
        return 2
    if ref and not REF_RE.fullmatch(ref):
        print("Invalid Git ref", file=sys.stderr)
        return 2
    if REPO.exists():
        print("Workspace repository already exists", file=sys.stderr)
        return 2

    ROOT.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update({"GIT_TERMINAL_PROMPT": "0", "HOME": "/tmp"})
    if token:
        env = github_git_environment(token)

    command = ["git", "-c", "credential.helper=", "clone", "--depth", "1", "--no-tags"]
    if ref:
        command += ["--branch", ref]
    command += [repository_url, str(REPO)]

    result = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=180,
        check=False,
    )

    if result.returncode != 0:
        print("Repository clone failed safely", file=sys.stderr)
        return 3

    metadata = {
        "repository_url": repository_url,
        "ref": ref or None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "bootstrap_authenticated": bool(token),
    }
    (ROOT / "workspace.json").write_text(json.dumps(metadata), encoding="utf-8")
    try:
        _chown_tree(ROOT, uid, gid)
    except (OSError, RuntimeError) as exc:
        print(f"Workspace ownership normalization failed: {exc}", file=sys.stderr)
        return 4
    print("bootstrap-ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
