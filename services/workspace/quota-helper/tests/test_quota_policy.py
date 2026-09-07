import ctypes
import errno
import os
import stat

import pytest

from app.main import (
    BASIC_BLOCK_BYTES,
    FS_DQ_BLOCK_LIMITS,
    FS_PROJ_QUOTA,
    FsDiskQuota,
    MAX_PROJECT_ID,
    MIN_PROJECT_ID,
    PROJECT_QUOTA_FAILURE_ERRNO,
    PROJECT_QUOTA_FAILURE_ERRNO_NAME,
    Q_XGETQUOTA,
    Q_XGETQUOTA_PROJECT,
    Q_XSETQLIM,
    Q_XSETQLIM_PROJECT,
    WORKSPACE_ID_RE,
    XQM_PRJQUOTA,
    _candidate_project_ids,
    _parse_project_id,
    _project_inherit_enabled,
    _qcmd,
    _quota_basic_blocks,
    _quota_record,
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


def test_xfs_project_quota_failure_uses_enospc():
    assert PROJECT_QUOTA_FAILURE_ERRNO == errno.ENOSPC
    assert PROJECT_QUOTA_FAILURE_ERRNO_NAME == "ENOSPC"


def test_project_assignment_verification_parses_exact_numeric_id():
    assert _parse_project_id("projid = 1563961394\n") == 1563961394
    with pytest.raises(RuntimeError, match="verify XFS project id"):
        _parse_project_id("projid unavailable\n")


def test_project_inheritance_verification_requires_p_flag():
    assert _project_inherit_enabled("----------------P-- /quota-root/workspace\n") is True
    assert _project_inherit_enabled("------------------- /quota-root/workspace\n") is False


def test_xfs_quotactl_fd_command_encoding_is_project_scoped():
    assert Q_XGETQUOTA_PROJECT == _qcmd(Q_XGETQUOTA, XQM_PRJQUOTA)
    assert Q_XSETQLIM_PROJECT == _qcmd(Q_XSETQLIM, XQM_PRJQUOTA)
    assert Q_XGETQUOTA_PROJECT & 0xFF == XQM_PRJQUOTA
    assert Q_XSETQLIM_PROJECT & 0xFF == XQM_PRJQUOTA


def test_fs_disk_quota_abi_and_block_limit_record_for_amd64_staging():
    assert ctypes.sizeof(FsDiskQuota) == 112
    limit_bytes = 1024 * 1024
    record = _quota_record(123456, limit_bytes)
    assert BASIC_BLOCK_BYTES == 512
    assert _quota_basic_blocks(limit_bytes) == 2048
    assert record.d_id == 123456
    assert record.d_flags == FS_PROJ_QUOTA
    assert record.d_fieldmask == FS_DQ_BLOCK_LIMITS
    assert record.d_blk_softlimit == 2048
    assert record.d_blk_hardlimit == 2048


def test_reclaim_tree_restores_traversal_top_down_without_following_symlinks(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    locked = workspace / "locked"
    outside = tmp_path / "outside"
    workspace.mkdir()
    locked.mkdir()
    outside.mkdir()
    data_file = locked / "file.txt"
    data_file.write_text("data", encoding="utf-8")
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
    assert data_file.read_text(encoding="utf-8") == "data"
    assert os.fspath(workspace) in chowned
    assert os.fspath(locked) in chowned
    assert os.fspath(link) not in chowned
