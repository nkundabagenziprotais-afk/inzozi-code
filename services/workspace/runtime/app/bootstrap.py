from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys

GITHUB_HTTPS_RE = re.compile(r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?$")
REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,159}$")
ROOT = Path("/workspace")
REPO = ROOT / "repo"


def _chown_tree(root: Path, uid: int, gid: int) -> None:
    os.chown(root, uid, gid, follow_symlinks=False)
    for current, dirs, files in os.walk(root, followlinks=False):
        base = Path(current)
        for name in dirs + files:
            path = base / name
            try:
                os.chown(path, uid, gid, follow_symlinks=False)
            except FileNotFoundError:
                continue


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
    askpass = Path("/tmp/inzozi-git-askpass")
    env = os.environ.copy()
    env.update({"GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS_REQUIRE": "force", "HOME": "/tmp"})
    if token:
        askpass.write_text(
            "#!/bin/sh\n"
            "case \"$1\" in\n"
            "  *Username*) printf '%s\\n' 'x-access-token' ;;\n"
            "  *) printf '%s\\n' \"$WORKSPACE_GIT_TOKEN\" ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        askpass.chmod(0o700)
        env["GIT_ASKPASS"] = str(askpass)

    command = ["git", "-c", "credential.helper=", "clone", "--depth", "1", "--no-tags"]
    if ref:
        command += ["--branch", ref]
    command += [repository_url, str(REPO)]

    try:
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
    finally:
        askpass.unlink(missing_ok=True)

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
    _chown_tree(ROOT, uid, gid)
    print("bootstrap-ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
