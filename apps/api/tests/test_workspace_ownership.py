from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.routes.auth import _FAILED_LOGINS, router as auth_router
from app.security.auth import AuthMiddleware, hash_password
from app.security.workspace_ownership import WorkspaceOwnership, principal_can_access, workspace_namespace
from app.security.workspace_scope import WorkspaceOwnershipMiddleware

WORKSPACE_ID = "a" * 32


def _configure(monkeypatch, *, role: str = "developer") -> None:
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_BOOTSTRAP_EMAIL", "developer@inzozidigital.com")
    monkeypatch.setenv("AUTH_PASSWORD_HASH", hash_password("a-secure-staging-password", salt=b"2" * 16))
    monkeypatch.setenv("AUTH_SESSION_SECRET", "s" * 64)
    monkeypatch.setenv("AUTH_BOOTSTRAP_ROLE", role)
    monkeypatch.setenv("AUTH_ORGANIZATION_ID", "inzozi-digital")
    monkeypatch.setenv("AUTH_SESSION_TTL_MINUTES", "60")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    monkeypatch.setenv("WORKSPACE_OWNERSHIP_ENFORCED", "true")
    get_settings.cache_clear()
    _FAILED_LOGINS.clear()


def _ownership(owner_email: str = "developer@inzozidigital.com", organization_id: str = "inzozi-digital") -> WorkspaceOwnership:
    return WorkspaceOwnership(
        workspace_id=WORKSPACE_ID,
        organization_id=organization_id,
        owner_email=owner_email,
        owner_role="developer",
        repository_url="https://github.com/example/private-repo",
        namespace=workspace_namespace(organization_id, owner_email),
        created_at=datetime.now(timezone.utc),
        deleted_at=None,
    )


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(WorkspaceOwnershipMiddleware)
    app.add_middleware(AuthMiddleware)
    app.include_router(auth_router)

    @app.post("/v1/workspaces")
    def create_workspace() -> dict:
        return {
            "workspace_id": WORKSPACE_ID,
            "repository_url": "https://github.com/example/private-repo",
            "status": "ready",
        }

    @app.get("/v1/workspaces/{workspace_id}/tree")
    def tree(workspace_id: str) -> dict:
        return {"workspace_id": workspace_id, "status": "visible"}

    return app


def _login(client: TestClient) -> None:
    response = client.post(
        "/v1/auth/login",
        json={"email": "developer@inzozidigital.com", "password": "a-secure-staging-password"},
    )
    assert response.status_code == 200


def test_workspace_namespace_is_stable_and_owner_specific():
    first = workspace_namespace("inzozi-digital", "Developer@InzoziDigital.com")
    second = workspace_namespace("inzozi-digital", "developer@inzozidigital.com")
    other = workspace_namespace("inzozi-digital", "reviewer@inzozidigital.com")
    assert first == second
    assert first != other
    assert len(first) == 32


def test_principal_access_is_org_and_owner_scoped():
    class Principal:
        organization_id = "inzozi-digital"
        email = "developer@inzozidigital.com"
        role = "developer"

    principal = Principal()
    assert principal_can_access(principal, _ownership()) is True
    assert principal_can_access(principal, _ownership(owner_email="other@inzozidigital.com")) is False
    assert principal_can_access(principal, _ownership(organization_id="other-org")) is False


def test_successful_workspace_create_registers_authenticated_owner(monkeypatch):
    _configure(monkeypatch, role="developer")
    captured = {}

    def register(**kwargs):
        captured.update(kwargs)
        return _ownership()

    monkeypatch.setattr("app.security.workspace_scope.register_workspace", register)
    monkeypatch.setattr("app.security.workspace_scope.record_workspace_audit", lambda **_kwargs: None)

    with TestClient(_app()) as client:
        _login(client)
        response = client.post(
            "/v1/workspaces",
            json={"repository_url": "https://github.com/example/private-repo"},
        )
        assert response.status_code == 200
        assert response.json()["workspace_id"] == WORKSPACE_ID
        assert captured["workspace_id"] == WORKSPACE_ID
        assert captured["repository_url"] == "https://github.com/example/private-repo"
        assert captured["principal"].email == "developer@inzozidigital.com"
    get_settings.cache_clear()


def test_cross_owner_workspace_id_guess_returns_404(monkeypatch):
    _configure(monkeypatch, role="developer")
    monkeypatch.setattr("app.security.workspace_scope.get_workspace_ownership", lambda _workspace_id: _ownership("other@inzozidigital.com"))
    monkeypatch.setattr("app.security.workspace_scope.record_workspace_audit", lambda **_kwargs: None)

    with TestClient(_app()) as client:
        _login(client)
        response = client.get(f"/v1/workspaces/{WORKSPACE_ID}/tree")
        assert response.status_code == 404
        assert response.json()["detail"] == "Workspace not found"
    get_settings.cache_clear()


def test_platform_owner_can_access_same_org_workspace(monkeypatch):
    _configure(monkeypatch, role="platform_owner")
    monkeypatch.setattr("app.security.workspace_scope.get_workspace_ownership", lambda _workspace_id: _ownership("other@inzozidigital.com"))

    with TestClient(_app()) as client:
        _login(client)
        response = client.get(f"/v1/workspaces/{WORKSPACE_ID}/tree")
        assert response.status_code == 200
    get_settings.cache_clear()
