from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

import app.routes.git_pull_request as git_pull_request
from app.main import app

client = TestClient(app)
WORKSPACE_ID = "a" * 32
REPOSITORY_URL = "https://github.com/nkundabagenziprotais-afk/inzozi-code"


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


def test_prepare_pull_request_binds_exact_review_state(monkeypatch):
    async def fake_review(workspace_id: str):
        assert workspace_id == WORKSPACE_ID
        return clean_review()

    async def fake_request(method: str, path: str, **kwargs):
        assert method == "GET"
        assert path.endswith("/metadata")
        return {"repository_url": REPOSITORY_URL}

    monkeypatch.setattr(git_pull_request, "_review_snapshot", fake_review)
    monkeypatch.setattr(git_pull_request, "_request", fake_request)
    monkeypatch.setattr(git_pull_request, "default_installation_for", lambda url: 456)
    git_pull_request.PULL_REQUEST_APPROVALS.clear()

    response = client.post(
        f"/v1/workspaces/{WORKSPACE_ID}/git/pull-request/prepare",
        json={
            "title": "feat: reviewed change",
            "body": "Human-reviewed change from Inzozi Code.",
            "base_branch": "main",
        },
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["head_branch"] == "feature/reviewed-change"
    assert payload["base_branch"] == "main"
    assert payload["commit_sha"] == "b" * 40
    assert payload["draft"] is True
    assert payload["requires_human_approval"] is True
    assert payload["merge_enabled"] is False
    assert payload["approval_id"] in git_pull_request.PULL_REQUEST_APPROVALS


def test_prepare_pull_request_rejects_dirty_or_unsafe_state(monkeypatch):
    dirty = clean_review()
    dirty["dirty"] = True
    dirty["changed_paths"] = ["app/main.py"]

    async def dirty_review(workspace_id: str):
        return dirty

    monkeypatch.setattr(git_pull_request, "_review_snapshot", dirty_review)
    response = client.post(
        f"/v1/workspaces/{WORKSPACE_ID}/git/pull-request/prepare",
        json={"title": "feat: reviewed change", "base_branch": "main"},
    )
    assert response.status_code == 409


def test_prepare_pull_request_rejects_unapproved_base_branch(monkeypatch):
    async def fake_review(workspace_id: str):
        return clean_review()

    monkeypatch.setattr(git_pull_request, "_review_snapshot", fake_review)
    response = client.post(
        f"/v1/workspaces/{WORKSPACE_ID}/git/pull-request/prepare",
        json={"title": "feat: reviewed change", "base_branch": "production"},
    )
    assert response.status_code == 400


def test_approve_pull_request_uses_least_privilege_and_remote_sha_check(monkeypatch):
    approval_id = "pr-approval-test"
    git_pull_request.PULL_REQUEST_APPROVALS.clear()
    git_pull_request.PULL_REQUEST_APPROVALS[approval_id] = {
        "workspace_id": WORKSPACE_ID,
        "repository_url": REPOSITORY_URL,
        "installation_id": 456,
        "branch": "feature/reviewed-change",
        "commit_sha": "b" * 40,
        "title": "feat: reviewed change",
        "body": "Reviewed body",
        "base_branch": "main",
        "created_at": datetime.now(timezone.utc),
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=5),
    }

    async def fake_review(workspace_id: str):
        return clean_review()

    token_calls = []

    async def fake_token(
        installation_id: int,
        repository_url: str,
        *,
        contents_permission: str = "read",
        pull_requests_permission: str | None = None,
    ):
        token_calls.append((installation_id, repository_url, contents_permission, pull_requests_permission))
        return "short-lived-pr-token"

    remote_calls = []

    async def fake_remote_head(repository_url: str, branch: str, token: str):
        remote_calls.append((repository_url, branch, token))
        return "b" * 40

    created = {}

    async def fake_create(repository_url: str, **kwargs):
        created.update({"repository_url": repository_url, **kwargs})
        return {
            "status": "created",
            "number": 42,
            "html_url": "https://github.com/nkundabagenziprotais-afk/inzozi-code/pull/42",
            "draft": True,
        }

    monkeypatch.setattr(git_pull_request, "_review_snapshot", fake_review)
    monkeypatch.setattr(git_pull_request, "create_installation_token", fake_token)
    monkeypatch.setattr(git_pull_request, "get_remote_branch_head", fake_remote_head)
    monkeypatch.setattr(git_pull_request, "create_or_get_draft_pull_request", fake_create)

    response = client.post(
        f"/v1/workspaces/{WORKSPACE_ID}/git/pull-request/approve",
        json={"approval_id": approval_id},
    )
    assert response.status_code == 200
    assert token_calls == [(456, REPOSITORY_URL, "read", "write")]
    assert remote_calls == [(REPOSITORY_URL, "feature/reviewed-change", "short-lived-pr-token")]
    assert created["branch"] == "feature/reviewed-change"
    assert created["base_branch"] == "main"
    assert created["token"] == "short-lived-pr-token"
    assert response.json()["pull_request_number"] == 42
    assert response.json()["draft"] is True
    assert response.json()["merged"] is False
    assert response.json()["deployment_started"] is False
    assert approval_id not in git_pull_request.PULL_REQUEST_APPROVALS


def test_approve_pull_request_rejects_remote_sha_mismatch(monkeypatch):
    approval_id = "pr-stale-remote"
    git_pull_request.PULL_REQUEST_APPROVALS.clear()
    git_pull_request.PULL_REQUEST_APPROVALS[approval_id] = {
        "workspace_id": WORKSPACE_ID,
        "repository_url": REPOSITORY_URL,
        "installation_id": 456,
        "branch": "feature/reviewed-change",
        "commit_sha": "b" * 40,
        "title": "feat: reviewed change",
        "body": "Reviewed body",
        "base_branch": "main",
        "created_at": datetime.now(timezone.utc),
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=5),
    }

    async def fake_review(workspace_id: str):
        return clean_review()

    async def fake_token(*args, **kwargs):
        return "short-lived-pr-token"

    async def changed_remote(*args, **kwargs):
        return "c" * 40

    monkeypatch.setattr(git_pull_request, "_review_snapshot", fake_review)
    monkeypatch.setattr(git_pull_request, "create_installation_token", fake_token)
    monkeypatch.setattr(git_pull_request, "get_remote_branch_head", changed_remote)

    response = client.post(
        f"/v1/workspaces/{WORKSPACE_ID}/git/pull-request/approve",
        json={"approval_id": approval_id},
    )
    assert response.status_code == 409
    assert approval_id not in git_pull_request.PULL_REQUEST_APPROVALS
