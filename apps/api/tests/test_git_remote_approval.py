from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

import app.routes.git_remote as git_remote
from app.main import app

client = TestClient(app)
WORKSPACE_ID = "a" * 32


def clean_review(branch: str = "feature/reviewed-change", head: str = "b" * 40) -> dict:
    return {
        "branch": branch,
        "head": head,
        "status": f"## {branch}\n",
        "diff": "",
        "fingerprint": "fingerprint",
        "protected_branch": False,
        "changed_paths": [],
        "dirty": False,
    }


def test_prepare_push_binds_approval_to_clean_safe_branch(monkeypatch):
    async def fake_review(workspace_id: str):
        assert workspace_id == WORKSPACE_ID
        return clean_review()

    async def fake_request(method: str, path: str, **kwargs):
        assert method == "GET"
        assert path.endswith("/metadata")
        return {"repository_url": "https://github.com/nkundabagenziprotais-afk/inzozi-code"}

    monkeypatch.setattr(git_remote, "_review_snapshot", fake_review)
    monkeypatch.setattr(git_remote, "_request", fake_request)
    monkeypatch.setattr(git_remote, "default_installation_for", lambda url: 456)
    git_remote.PUSH_APPROVALS.clear()

    response = client.post(f"/v1/workspaces/{WORKSPACE_ID}/git/push/prepare")
    assert response.status_code == 201
    payload = response.json()
    assert payload["branch"] == "feature/reviewed-change"
    assert payload["commit_sha"] == "b" * 40
    assert payload["requires_human_approval"] is True
    assert payload["force_push"] is False
    assert payload["pull_request_created"] is False
    assert payload["approval_id"] in git_remote.PUSH_APPROVALS


def test_prepare_push_rejects_dirty_worktree(monkeypatch):
    review = clean_review()
    review["dirty"] = True
    review["changed_paths"] = ["app/main.py"]

    async def fake_review(workspace_id: str):
        return review

    monkeypatch.setattr(git_remote, "_review_snapshot", fake_review)
    response = client.post(f"/v1/workspaces/{WORKSPACE_ID}/git/push/prepare")
    assert response.status_code == 409


def test_approve_push_mints_write_token_only_after_recheck(monkeypatch):
    approval_id = "approval-test"
    repository_url = "https://github.com/nkundabagenziprotais-afk/inzozi-code"
    git_remote.PUSH_APPROVALS.clear()
    git_remote.PUSH_APPROVALS[approval_id] = {
        "workspace_id": WORKSPACE_ID,
        "repository_url": repository_url,
        "installation_id": 456,
        "branch": "feature/reviewed-change",
        "commit_sha": "b" * 40,
        "created_at": datetime.now(timezone.utc),
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=5),
    }

    async def fake_review(workspace_id: str):
        return clean_review()

    token_calls = []

    async def fake_token(installation_id: int, url: str, *, contents_permission: str = "read"):
        token_calls.append((installation_id, url, contents_permission))
        return "short-lived-write-token"

    forwarded = {}

    async def fake_request(method: str, path: str, **kwargs):
        forwarded.update({"method": method, "path": path, **kwargs})
        return {"status": "pushed"}

    monkeypatch.setattr(git_remote, "_review_snapshot", fake_review)
    monkeypatch.setattr(git_remote, "create_installation_token", fake_token)
    monkeypatch.setattr(git_remote, "_request", fake_request)

    response = client.post(
        f"/v1/workspaces/{WORKSPACE_ID}/git/push/approve",
        json={"approval_id": approval_id},
    )
    assert response.status_code == 200
    assert token_calls == [(456, repository_url, "write")]
    assert forwarded["method"] == "POST"
    assert forwarded["path"].endswith("/git/push")
    assert forwarded["json"]["branch"] == "feature/reviewed-change"
    assert forwarded["json"]["expected_head"] == "b" * 40
    assert forwarded["json"]["git_token"] == "short-lived-write-token"
    assert response.json()["forced"] is False
    assert response.json()["pull_request_created"] is False
    assert approval_id not in git_remote.PUSH_APPROVALS


def test_approve_push_invalidates_when_commit_changes(monkeypatch):
    approval_id = "stale-approval"
    git_remote.PUSH_APPROVALS.clear()
    git_remote.PUSH_APPROVALS[approval_id] = {
        "workspace_id": WORKSPACE_ID,
        "repository_url": "https://github.com/nkundabagenziprotais-afk/inzozi-code",
        "installation_id": 456,
        "branch": "feature/reviewed-change",
        "commit_sha": "b" * 40,
        "created_at": datetime.now(timezone.utc),
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=5),
    }

    async def changed_review(workspace_id: str):
        return clean_review(head="c" * 40)

    monkeypatch.setattr(git_remote, "_review_snapshot", changed_review)
    response = client.post(
        f"/v1/workspaces/{WORKSPACE_ID}/git/push/approve",
        json={"approval_id": approval_id},
    )
    assert response.status_code == 409
    assert approval_id not in git_remote.PUSH_APPROVALS
