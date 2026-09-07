from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.entrypoint import (
    _install_git_safe_directory,
    _runtime_repo,
    _safe_repo_for_command,
    _validated_repo,
)


def _repo(root: Path, workspace_id: str = "a" * 32) -> Path:
    repo = root / workspace_id / "repo"
    (repo / ".git" / "refs" / "heads").mkdir(parents=True)
    return repo


def test_runtime_repo_requires_one_strict_broker_workspace(tmp_path):
    repo = _repo(tmp_path)
    assert _runtime_repo(tmp_path) == repo.resolve()

    _repo(tmp_path, "b" * 32)
    with pytest.raises(RuntimeError, match="exactly one"):
        _runtime_repo(tmp_path)


def test_runtime_repo_ignores_non_workspace_names(tmp_path):
    ignored = tmp_path / "not-a-workspace" / "repo"
    (ignored / ".git" / "refs" / "heads").mkdir(parents=True)
    repo = _repo(tmp_path)
    assert _runtime_repo(tmp_path) == repo.resolve()


def test_validated_repo_rejects_symlinked_git_metadata(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside-git"
    outside.mkdir()
    (repo / ".git").symlink_to(outside, target_is_directory=True)
    with pytest.raises(RuntimeError, match="metadata"):
        _validated_repo(repo)


def test_validated_repo_rejects_unwritable_git_metadata(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    git_dir = repo / ".git"
    real_access = os.access

    def fake_access(path, mode):
        if Path(path) == git_dir:
            return False
        return real_access(path, mode)

    monkeypatch.setattr(os, "access", fake_access)
    with pytest.raises(RuntimeError, match="not writable"):
        _validated_repo(repo)


def test_git_safe_directory_is_exact_and_process_scoped(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    for key in ("GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0"):
        monkeypatch.delenv(key, raising=False)

    _install_git_safe_directory(repo)

    assert os.environ["GIT_CONFIG_COUNT"] == "1"
    assert os.environ["GIT_CONFIG_KEY_0"] == "safe.directory"
    assert os.environ["GIT_CONFIG_VALUE_0"] == str(repo.resolve())
    assert os.environ["GIT_CONFIG_VALUE_0"] != "*"


def test_non_git_helper_commands_do_not_receive_safe_directory():
    assert _safe_repo_for_command(["python", "-m", "app.bootstrap"]) is None
    assert _safe_repo_for_command(["sh", "-lc", "du -sb /workspace"]) is None
