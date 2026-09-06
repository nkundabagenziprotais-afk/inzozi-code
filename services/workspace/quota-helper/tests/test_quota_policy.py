import re

from app.main import (
    MAX_PROJECT_ID,
    MIN_PROJECT_ID,
    WORKSPACE_ID_RE,
    _candidate_project_ids,
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
