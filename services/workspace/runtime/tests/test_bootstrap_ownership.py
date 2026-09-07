from __future__ import annotations

import os
from pathlib import Path

import pytest

from app import bootstrap


def test_chown_tree_transfers_workspace_root_last(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    heads = root / "repo" / ".git" / "refs" / "heads"
    heads.mkdir(parents=True)
    (heads / "main").write_text("abc\n", encoding="utf-8")
    (root / "workspace.json").write_text("{}", encoding="utf-8")

    calls: list[Path] = []

    def record_owner(path: Path, uid: int, gid: int) -> None:
        assert uid == 10002
        assert gid == 10002
        calls.append(Path(path))

    monkeypatch.setattr(bootstrap, "_set_owner", record_owner)
    bootstrap._chown_tree(root, 10002, 10002)

    assert calls[-1] == root
    assert calls.index(heads / "main") < calls.index(heads)
    assert calls.index(heads) < calls.index(heads.parent)
    assert calls.index(root / "repo") < calls.index(root)


def test_set_owner_fails_when_ownership_does_not_change(tmp_path, monkeypatch):
    target = tmp_path / "target"
    target.write_text("data", encoding="utf-8")

    monkeypatch.setattr(os, "chown", lambda *args, **kwargs: None)
    impossible_uid = target.lstat().st_uid + 10000

    with pytest.raises(RuntimeError, match="ownership verification failed"):
        bootstrap._set_owner(target, impossible_uid, target.lstat().st_gid)


def test_chown_tree_rejects_symlinked_workspace_root(tmp_path):
    real_root = tmp_path / "real"
    real_root.mkdir()
    linked_root = tmp_path / "linked"
    linked_root.symlink_to(real_root, target_is_directory=True)

    with pytest.raises(RuntimeError, match="unavailable or symlinked"):
        bootstrap._chown_tree(linked_root, 10002, 10002)
