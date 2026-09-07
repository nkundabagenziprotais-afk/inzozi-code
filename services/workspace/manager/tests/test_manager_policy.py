import pytest
from fastapi import HTTPException

import app.main as manager


def test_runtime_name_is_workspace_scoped():
    workspace_id = "a" * 32
    assert manager._runtime_name(workspace_id) == f"inzozi-ws-{workspace_id}"


def test_invalid_workspace_id_is_rejected():
    with pytest.raises(ValueError):
        manager._workspace_id("../../docker.sock")


def test_clone_allowlist_is_github_https_only():
    assert manager.GITHUB_HTTPS_RE.fullmatch("https://github.com/inzozi/example")
    assert not manager.GITHUB_HTTPS_RE.fullmatch("http://github.com/inzozi/example")
    assert not manager.GITHUB_HTTPS_RE.fullmatch("https://example.com/inzozi/example")
    assert not manager.GITHUB_HTTPS_RE.fullmatch("file:///etc/passwd")


def test_remote_push_remains_limited_to_safe_nonproduction_branches():
    assert manager.SAFE_PUSH_BRANCH_RE.fullmatch("feature/reviewed-change")
    assert manager.SAFE_PUSH_BRANCH_RE.fullmatch("fix/login-loop")
    assert not manager.SAFE_PUSH_BRANCH_RE.fullmatch("main")
    assert not manager.SAFE_PUSH_BRANCH_RE.fullmatch("production")
    assert not manager.SAFE_PUSH_BRANCH_RE.fullmatch("../../escape")


def test_manager_fails_closed_without_broker_secret(monkeypatch):
    monkeypatch.setattr(manager, "BROKER_TOKEN", "")
    with pytest.raises(HTTPException) as exc:
        manager._broker_headers()
    assert exc.value.status_code == 503


def test_manager_uses_bearer_auth_for_broker(monkeypatch):
    token = "a" * 64
    monkeypatch.setattr(manager, "BROKER_TOKEN", token)
    assert manager._broker_headers() == {"Authorization": f"Bearer {token}"}
