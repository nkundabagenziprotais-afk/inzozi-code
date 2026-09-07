from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.core.config import get_settings
from app.routes.auth import _FAILED_LOGINS, router as auth_router
from app.security.auth import (
    AuthenticationError,
    AuthMiddleware,
    ROLE_PERMISSIONS,
    create_session_token,
    decode_session_token,
    hash_password,
    permission_for_request,
    verify_password,
)


def _configure_auth(monkeypatch, *, role: str = "platform_owner") -> None:
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_BOOTSTRAP_EMAIL", "owner@inzozidigital.com")
    monkeypatch.setenv("AUTH_PASSWORD_HASH", hash_password("a-secure-staging-password", salt=b"1" * 16))
    monkeypatch.setenv("AUTH_SESSION_SECRET", "s" * 64)
    monkeypatch.setenv("AUTH_BOOTSTRAP_ROLE", role)
    monkeypatch.setenv("AUTH_ORGANIZATION_ID", "inzozi-digital")
    monkeypatch.setenv("AUTH_SESSION_TTL_MINUTES", "60")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    get_settings.cache_clear()
    _FAILED_LOGINS.clear()


def _test_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(AuthMiddleware)
    app.include_router(auth_router)

    @app.get("/v1/workspaces/runtime")
    def read_workspace() -> dict:
        return {"status": "readable"}

    @app.post("/v1/workspaces")
    def create_workspace() -> dict:
        return {"status": "created"}

    return app


def test_password_hash_is_salted_and_verifiable():
    first = hash_password("a-secure-staging-password", salt=b"a" * 16)
    second = hash_password("a-secure-staging-password", salt=b"b" * 16)
    assert first != second
    assert verify_password("a-secure-staging-password", first) is True
    assert verify_password("wrong-password-value", first) is False
    assert first.startswith("pbkdf2_sha256$600000$")


def test_session_signature_rejects_tampering(monkeypatch):
    _configure_auth(monkeypatch)
    token, _ = create_session_token("owner@inzozidigital.com", "platform_owner", "inzozi-digital")
    principal = decode_session_token(token)
    assert principal.role == "platform_owner"
    assert principal.has("git:push") is True

    payload, signature = token.split(".", 1)
    replacement = "A" if signature[0] != "A" else "B"
    tampered = f"{payload}.{replacement}{signature[1:]}"
    with pytest.raises(AuthenticationError):
        decode_session_token(tampered)
    get_settings.cache_clear()


def test_permission_mapping_keeps_git_gates_separate():
    assert permission_for_request("POST", "/v1/workspaces/" + "a" * 32 + "/git/commit/prepare") == "git:commit"
    assert permission_for_request("POST", "/v1/workspaces/" + "a" * 32 + "/git/push/prepare") == "git:push"
    assert permission_for_request("POST", "/v1/workspaces/" + "a" * 32 + "/git/pull-request/prepare") == "git:pull_request"
    assert permission_for_request("PUT", "/v1/workspaces/" + "a" * 32 + "/files/README.md") == "workspace:edit"
    assert "deployment:approve" not in ROLE_PERMISSIONS["developer"]
    assert "git:push" not in ROLE_PERMISSIONS["reviewer"]


def test_login_issues_httponly_session_and_me_returns_role(monkeypatch):
    _configure_auth(monkeypatch)
    with TestClient(_test_app()) as client:
        assert client.get("/v1/auth/me").status_code == 401
        response = client.post(
            "/v1/auth/login",
            json={"email": "owner@inzozidigital.com", "password": "a-secure-staging-password"},
        )
        assert response.status_code == 200
        assert "HttpOnly" in response.headers.get("set-cookie", "")
        assert "SameSite=strict" in response.headers.get("set-cookie", "")
        me = client.get("/v1/auth/me")
        assert me.status_code == 200
        assert me.json()["role"] == "platform_owner"
        assert me.json()["organization_id"] == "inzozi-digital"
    get_settings.cache_clear()


def test_reviewer_can_read_but_direct_workspace_create_is_forbidden(monkeypatch):
    _configure_auth(monkeypatch, role="reviewer")
    with TestClient(_test_app()) as client:
        login = client.post(
            "/v1/auth/login",
            json={"email": "owner@inzozidigital.com", "password": "a-secure-staging-password"},
        )
        assert login.status_code == 200
        assert client.get("/v1/workspaces/runtime").status_code == 200
        denied = client.post("/v1/workspaces")
        assert denied.status_code == 403
        assert denied.json()["detail"] == "Your role does not allow this action"
    get_settings.cache_clear()
