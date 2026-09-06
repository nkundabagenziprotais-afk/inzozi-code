from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys

GITHUB_HTTPS_RE = re.compile(r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?$")
SAFE_BRANCH_RE = re.compile(r"^(feature|fix|ui|hotfix|deploy)/[a-z0-9][a-z0-9._-]{2,80}$")
HEAD_RE = re.compile(r"^[0-9a-f]{40,64}$")
ROOT = Path("/workspace")
REPO = ROOT / "repo"
METADATA = ROOT / "workspace.json"


def _run(*args: str) -> tuple[int, str]:
    result = subprocess.run(
        list(args),
        cwd=REPO,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=120,
        check=False,
    )
    return result.returncode, result.stdout.decode("utf-8", errors="replace")


def main() -> int:
    branch = os.getenv("WORKSPACE_BRANCH", "").strip()
    expected_head = os.getenv("WORKSPACE_EXPECTED_HEAD", "").strip()
    token = os.getenv("WORKSPACE_GIT_TOKEN", "")

    if not SAFE_BRANCH_RE.fullmatch(branch) or not HEAD_RE.fullmatch(expected_head) or not token:
        print("Invalid remote push request", file=sys.stderr)
        return 2
    if not REPO.is_dir() or not METADATA.is_file():
        print("Workspace repository metadata unavailable", file=sys.stderr)
        return 2

    try:
        metadata = json.loads(METADATA.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        print("Workspace repository metadata invalid", file=sys.stderr)
        return 2
    repository_url = metadata.get("repository_url")
    if not isinstance(repository_url, str) or not GITHUB_HTTPS_RE.fullmatch(repository_url):
        print("Workspace repository URL invalid", file=sys.stderr)
        return 2

    branch_rc, current_branch = _run("git", "branch", "--show-current")
    head_rc, current_head = _run("git", "rev-parse", "HEAD")
    status_rc, status = _run("git", "status", "--porcelain")
    if branch_rc or head_rc or status_rc:
        print("Unable to verify local Git state", file=sys.stderr)
        return 3
    if current_branch.strip() != branch or current_head.strip() != expected_head or status.strip():
        print("Local Git state changed after approval", file=sys.stderr)
        return 3

    askpass = Path("/tmp/inzozi-git-askpass")
    askpass.write_text(
        "#!/bin/sh\n"
        "case \"$1\" in\n"
        "  *Username*) printf '%s\\n' 'x-access-token' ;;\n"
        "  *) printf '%s\\n' \"$WORKSPACE_GIT_TOKEN\" ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    askpass.chmod(0o700)
    env = os.environ.copy()
    env.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ASKPASS_REQUIRE": "force",
            "GIT_ASKPASS": str(askpass),
            "HOME": "/tmp",
        }
    )
    try:
        result = subprocess.run(
            [
                "git",
                "-c",
                "credential.helper=",
                "-c",
                f"remote.origin.url={repository_url}",
                "push",
                "--porcelain",
                "origin",
                f"HEAD:refs/heads/{branch}",
            ],
            cwd=REPO,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=120,
            check=False,
        )
    finally:
        askpass.unlink(missing_ok=True)

    if result.returncode != 0:
        print("Remote push failed safely", file=sys.stderr)
        return 4

    print(
        json.dumps(
            {
                "status": "pushed",
                "repository_url": repository_url,
                "branch": branch,
                "commit_sha": expected_head,
                "forced": False,
            },
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
