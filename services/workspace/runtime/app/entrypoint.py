from __future__ import annotations

import os
from pathlib import Path
import re
import sys

WORKSPACE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
RUNTIME_ROOT = Path("/workspaces")
HELPER_REPO = Path("/workspace/repo")


def _validated_repo(path: Path) -> Path:
    if path.is_symlink() or not path.is_dir():
        raise RuntimeError("Workspace Git directory is unavailable or symlinked")
    git_dir = path / ".git"
    if git_dir.is_symlink() or not git_dir.is_dir():
        raise RuntimeError("Workspace Git metadata is unavailable or symlinked")
    refs_heads = git_dir / "refs" / "heads"
    if refs_heads.is_symlink() or not refs_heads.is_dir():
        raise RuntimeError("Workspace Git branch metadata is unavailable or symlinked")
    required_mode = os.R_OK | os.W_OK | os.X_OK
    if not os.access(path, required_mode):
        raise RuntimeError("Workspace repository is not writable by the runtime user")
    if not os.access(git_dir, required_mode) or not os.access(refs_heads, required_mode):
        raise RuntimeError("Workspace Git metadata is not writable by the runtime user")
    return path.resolve(strict=True)


def _runtime_repo(root: Path = RUNTIME_ROOT) -> Path:
    if root.is_symlink() or not root.is_dir():
        raise RuntimeError("Workspace runtime root is unavailable")
    candidates: list[Path] = []
    for child in root.iterdir():
        if child.is_symlink() or not child.is_dir() or not WORKSPACE_ID_RE.fullmatch(child.name):
            continue
        repo = child / "repo"
        if repo.is_dir() and not repo.is_symlink():
            candidates.append(repo)
    if len(candidates) != 1:
        raise RuntimeError("Workspace runtime must expose exactly one broker-derived repository")
    return _validated_repo(candidates[0])


def _is_python_module(argv: list[str], module: str) -> bool:
    if len(argv) < 3:
        return False
    return Path(argv[0]).name.startswith("python") and argv[1:3] == ["-m", module]


def _safe_repo_for_command(argv: list[str]) -> Path | None:
    if not argv:
        raise RuntimeError("Workspace runtime command is missing")
    if Path(argv[0]).name == "uvicorn":
        return _runtime_repo()
    if _is_python_module(argv, "app.remote_push"):
        return _validated_repo(HELPER_REPO)
    return None


def _install_git_safe_directory(repo: Path) -> None:
    canonical = _validated_repo(repo)
    os.environ["GIT_CONFIG_COUNT"] = "1"
    os.environ["GIT_CONFIG_KEY_0"] = "safe.directory"
    os.environ["GIT_CONFIG_VALUE_0"] = str(canonical)


def main() -> int:
    argv = sys.argv[1:]
    try:
        repo = _safe_repo_for_command(argv)
        if repo is not None:
            _install_git_safe_directory(repo)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 70
    os.execvp(argv[0], argv)
    return 70


if __name__ == "__main__":
    raise SystemExit(main())
