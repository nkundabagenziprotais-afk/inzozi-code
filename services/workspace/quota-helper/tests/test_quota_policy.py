import os
import re
import stat

from app.main import (
    MAX_PROJECT_ID,
    MIN_PROJECT_ID,
    WORKSPACE_ID_RE,
    _candidate_project_ids,
    _reclaim_tree_for_removal,
)


def test_workspace_ids_are_strict_lowercase_hex():
    assert WORKSPACE_ID_RE.fullmatch("a" * 32)
    assert not WORKSPACE_ID_RE.fullmatch("A" * 32)
    assert not WORKSPACE_ID_RE.fullmatch("../" + "a" * 29)


def test_project_id_candidates_are_deterministic_and_nonzero():
    workspace_id = "a" * 32
    first = list(_candidate_project_ids(workspace_id))
    second = list(_candidate_project_ids(workspace_id))
    assert first == second
    assert len(first) == 64
    assert len(set(first)) > 1
    assert all(MIN_PROJECT_ID <= item < MAX_PROJECT_ID for item in first)


def test_distinct_workspaces_do_not_share_the_first_candidate():
    left = next(_candidate_project_ids("a" * 32))
    right = next(_candidate_project_ids("b" * 32))
    assert left != right


def test_reclaim_tree_restores_traversal_top_down_without_following_symlinks(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    locked = workspace / "locked"
    outside = tmp_path / "outside"
    workspace.mkdir()
    locked.mkdir()
    outside.mkdir()
    (locked / "file.txt").write_text("data", encoding="utf-8")
    link = workspace / "outside-link"
    link.symlink_to(outside, target_is_directory=True)

    os.chmod(workspace, 0o700)
    os.chmod(locked, 0o000)
    os.chmod(outside, 0o755)

    chowned: list[str] = []

    def fake_chown(path, uid, gid, *, follow_symlinks=True):
        assert uid == 0
        assert gid == 0
        assert follow_symlinks is False
        chowned.append(os.fspath(path))

    monkeypatch.setattr(os, "chown", fake_chown)

    _reclaim_tree_for_removal(workspace)

    assert stat.S_IMODE(workspace.stat().st_mode) == 0o700
    assert stat.S_IMODE(locked.stat().st_mode) == 0o700
    assert stat.S_IMODE(outside.stat().st_mode) == 0o755
    assert os.fspath(workspace) in chowned
    assert os.fspath(locked) in chowned
    assert os.fspath(link) not in chowned
