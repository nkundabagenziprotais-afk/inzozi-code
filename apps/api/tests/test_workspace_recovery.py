from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.routes.auth import router as auth_router
from app.routes import workspace as workspace_routes
from app.security.auth import AuthMiddleware, AuthPrincipal, ROLE_PERMISSIONS, hash_password
from app.security.redis_controls import configure_auth_state_client
from app.security.workspace_ownership import (
    WorkspaceOwnership,
    list_accessible_active_workspaces,
    principal_can_access,
    workspace_namespace,
)
from app.security.workspace_scope import WorkspaceOwnershipMiddleware
from tests.fake_redis import FakeAsyncRedis

OWN_ID = "ee69efc608044cf2b69da605c47f1e7a"
OTHER_OWNER_ID = "58e1358ec63a4547a6ae81e803cae498"
OTHER_ORG_ID = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
DELETED_ID = "cccccccccccccccccccccccccccccccc"


def _configure(monkeypatch, *, role: str = "developer", email: str = "developer@inzozidigital.com") -> None:
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_BOOTSTRAP_EMAIL", email)
    monkeypatch.setenv("AUTH_PASSWORD_HASH", hash_password("a-secure-staging-password", salt=b"3" * 16))
    monkeypatch.setenv("AUTH_SESSION_SECRET", "s" * 64)
    monkeypatch.setenv("AUTH_BOOTSTRAP_ROLE", role)
    monkeypatch.setenv("AUTH_ORGANIZATION_ID", "inzozi-digital")
    monkeypatch.setenv("AUTH_SESSION_TTL_MINUTES", "60")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    monkeypatch.setenv("WORKSPACE_OWNERSHIP_ENFORCED", "true")
    get_settings.cache_clear()
    configure_auth_state_client(FakeAsyncRedis())


def _ownership(
    *,
    workspace_id: str,
    owner_email: str = "developer@inzozidigital.com",
    organization_id: str = "inzozi-digital",
    deleted_at: datetime | None = None,
    created_at: datetime | None = None,
) -> WorkspaceOwnership:
    return WorkspaceOwnership(
        workspace_id=workspace_id,
        organization_id=organization_id,
        owner_email=owner_email,
        owner_role="developer",
        repository_url="https://github.com/example/private-repo",
        namespace=workspace_namespace(organization_id, owner_email),
        created_at=created_at or datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc),
        deleted_at=deleted_at,
    )


def _principal(*, role: str = "developer", email: str = "developer@inzozidigital.com", organization_id: str = "inzozi-digital"):
    return AuthPrincipal(
        email=email,
        role=role,
        organization_id=organization_id,
        permissions=ROLE_PERMISSIONS[role],
        session_id="test-session",
        expires_at=None,
        user_id="11111111-1111-4111-8111-111111111111",
        session_version=0,
        auth_enabled=True,
    )


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(WorkspaceOwnershipMiddleware)
    app.add_middleware(AuthMiddleware)
    app.include_router(auth_router)
    app.include_router(workspace_routes.router)
    return app


def _login(client: TestClient, *, email: str = "developer@inzozidigital.com") -> None:
    response = client.post(
        "/v1/auth/login",
        json={"email": email, "password": "a-secure-staging-password"},
    )
    assert response.status_code == 200


class _FakeCursor:
    def __init__(self, rows: list[dict]):
        self.rows = rows
        self.query = ""
        self.params: tuple | None = None

    def execute(self, query: str, params=None):
        self.query = " ".join(query.split())
        self.params = params

    def fetchall(self):
        return self.rows

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_list_accessible_active_workspaces_scopes_developer(monkeypatch):
    rows = [
        {
            "workspace_id": OWN_ID,
            "organization_id": "inzozi-digital",
            "owner_email": "developer@inzozidigital.com",
            "owner_role": "developer",
            "repository_url": "https://github.com/example/private-repo",
            "namespace": "n" * 32,
            "created_at": datetime.now(timezone.utc),
            "deleted_at": None,
        }
    ]
    cursor = _FakeCursor(rows)
    monkeypatch.setattr(
        "app.security.workspace_ownership._connect",
        lambda: _FakeConnection(cursor),
    )

    result = list_accessible_active_workspaces(_principal(role="developer"))
    assert [item.workspace_id for item in result] == [OWN_ID]
    assert "deleted_at IS NULL" in cursor.query
    assert "organization_id = %s" in cursor.query
    assert "owner_user_id = %s" in cursor.query
    assert "owner_email = %s" in cursor.query
    assert cursor.params == (
        "inzozi-digital",
        "11111111-1111-4111-8111-111111111111",
        "developer@inzozidigital.com",
        20,
    )


def test_list_accessible_active_workspaces_privileged_same_org_only(monkeypatch):
    rows: list[dict] = []
    cursor = _FakeCursor(rows)
    monkeypatch.setattr(
        "app.security.workspace_ownership._connect",
        lambda: _FakeConnection(cursor),
    )

    list_accessible_active_workspaces(_principal(role="platform_owner", email="owner@inzozidigital.com"))
    assert "owner_email = %s" not in cursor.query
    assert cursor.params == ("inzozi-digital", 20)

    cursor_admin = _FakeCursor(rows)
    monkeypatch.setattr(
        "app.security.workspace_ownership._connect",
        lambda: _FakeConnection(cursor_admin),
    )
    list_accessible_active_workspaces(_principal(role="org_admin", email="admin@inzozidigital.com"))
    assert "owner_email = %s" not in cursor_admin.query
    assert cursor_admin.params == ("inzozi-digital", 20)


def test_principal_access_still_blocks_cross_org_and_deleted():
    principal = SimpleNamespace(
        organization_id="inzozi-digital",
        email="developer@inzozidigital.com",
        role="developer",
        user_id="11111111-1111-4111-8111-111111111111",
    )
    assert principal_can_access(principal, _ownership(workspace_id=OWN_ID)) is True
    assert principal_can_access(principal, _ownership(workspace_id=OTHER_OWNER_ID, owner_email="other@inzozidigital.com")) is False
    assert principal_can_access(principal, _ownership(workspace_id=OTHER_ORG_ID, organization_id="other-org")) is False
    assert (
        principal_can_access(
            principal,
            _ownership(workspace_id=DELETED_ID, deleted_at=datetime.now(timezone.utc)),
        )
        is False
    )


def test_recovery_returns_safe_fields_only(monkeypatch):
    _configure(monkeypatch, role="developer")
    ownerships = [
        _ownership(workspace_id=OWN_ID, created_at=datetime(2026, 9, 2, 10, 0, tzinfo=timezone.utc)),
    ]

    async def fake_request(method: str, path: str, **_kwargs):
        assert method == "GET"
        assert path == f"/v1/workspaces/{OWN_ID}/metadata"
        return {
            "repository_url": "https://github.com/example/private-repo",
            "ref": "main",
            "owner_email": "should-not-leak",
            "organization_id": "should-not-leak",
            "namespace": "should-not-leak",
            "docker_image": "should-not-leak",
            "network_name": "should-not-leak",
            "quota_project_id": 99,
            "filesystem_path": "/srv/secret",
        }

    monkeypatch.setattr(workspace_routes, "list_accessible_active_workspaces", lambda _principal, limit=20: ownerships)
    monkeypatch.setattr(workspace_routes, "_request", fake_request)

    with TestClient(_app()) as client:
        _login(client)
        response = client.get("/v1/workspaces/recovery")
        assert response.status_code == 200
        payload = response.json()
        assert list(payload.keys()) == ["workspaces"]
        assert len(payload["workspaces"]) == 1
        item = payload["workspaces"][0]
        assert item == {
            "workspace_id": OWN_ID,
            "repository_url": "https://github.com/example/private-repo",
            "ref": "main",
            "created_at": "2026-09-02T10:00:00+00:00",
            "runtime_status": "ready",
        }
        forbidden = {
            "owner_email",
            "organization_id",
            "namespace",
            "owner_role",
            "docker_image",
            "network_name",
            "quota_project_id",
            "filesystem_path",
            "broker",
            "labels",
            "credentials",
        }
        assert forbidden.isdisjoint(item.keys())
    get_settings.cache_clear()


def test_recovery_hides_other_owner_and_other_org_from_developer(monkeypatch):
    _configure(monkeypatch, role="developer")
    # list_accessible_active_workspaces is the enforcement boundary; developer discovery
    # must never receive another owner's or another org's durable rows.
    monkeypatch.setattr(
        workspace_routes,
        "list_accessible_active_workspaces",
        lambda principal, limit=20: (
            []
            if principal.email.casefold() == "developer@inzozidigital.com"
            else [_ownership(workspace_id=OTHER_OWNER_ID, owner_email="other@inzozidigital.com")]
        ),
    )

    async def fail_metadata(*_args, **_kwargs):
        raise AssertionError("metadata must not be queried for undiscoverable workspaces")

    monkeypatch.setattr(workspace_routes, "_request", fail_metadata)

    with TestClient(_app()) as client:
        _login(client)
        response = client.get("/v1/workspaces/recovery")
        assert response.status_code == 200
        assert response.json() == {"workspaces": []}
    get_settings.cache_clear()


def test_recovery_platform_owner_and_org_admin_see_same_org_rows(monkeypatch):
    ownerships = [
        _ownership(workspace_id=OWN_ID, owner_email="developer@inzozidigital.com"),
        _ownership(workspace_id=OTHER_OWNER_ID, owner_email="other@inzozidigital.com"),
    ]

    async def fake_request(method: str, path: str, **_kwargs):
        workspace_id = path.split("/")[3]
        return {"repository_url": "https://github.com/example/private-repo", "ref": f"branch-{workspace_id[:4]}"}

    for role, email in (("platform_owner", "owner@inzozidigital.com"), ("org_admin", "admin@inzozidigital.com")):
        _configure(monkeypatch, role=role, email=email)
        monkeypatch.setattr(workspace_routes, "list_accessible_active_workspaces", lambda _principal, limit=20: ownerships)
        monkeypatch.setattr(workspace_routes, "_request", fake_request)
        with TestClient(_app()) as client:
            _login(client, email=email)
            response = client.get("/v1/workspaces/recovery")
            assert response.status_code == 200
            ids = [item["workspace_id"] for item in response.json()["workspaces"]]
            assert ids == [OWN_ID, OTHER_OWNER_ID]
            for item in response.json()["workspaces"]:
                assert "owner_email" not in item
                assert "organization_id" not in item
                assert "namespace" not in item
        get_settings.cache_clear()


def test_recovery_metadata_failure_marks_unavailable_without_failing_list(monkeypatch):
    _configure(monkeypatch, role="developer")
    ownerships = [
        _ownership(workspace_id=OWN_ID),
        _ownership(workspace_id=OTHER_OWNER_ID, created_at=datetime(2026, 8, 1, tzinfo=timezone.utc)),
    ]

    async def fake_request(method: str, path: str, **_kwargs):
        if OWN_ID in path:
            raise HTTPException(status_code=503, detail="Workspace runtime is unavailable")
        return {"repository_url": "https://github.com/example/private-repo", "ref": "feature/ok"}

    monkeypatch.setattr(workspace_routes, "list_accessible_active_workspaces", lambda _principal, limit=20: ownerships)
    monkeypatch.setattr(workspace_routes, "_request", fake_request)

    with TestClient(_app()) as client:
        _login(client)
        response = client.get("/v1/workspaces/recovery")
        assert response.status_code == 200
        payload = response.json()["workspaces"]
        assert len(payload) == 2
        by_id = {item["workspace_id"]: item for item in payload}
        assert by_id[OWN_ID]["runtime_status"] == "unavailable"
        assert by_id[OWN_ID]["ref"] is None
        assert by_id[OTHER_OWNER_ID]["runtime_status"] == "ready"
        assert by_id[OTHER_OWNER_ID]["ref"] == "feature/ok"
        assert "detail" not in by_id[OWN_ID]
        assert "Workspace runtime is unavailable" not in str(payload)
    get_settings.cache_clear()


def test_recovery_rejects_unauthenticated_request(monkeypatch):
    _configure(monkeypatch, role="developer")
    with TestClient(_app()) as client:
        response = client.get("/v1/workspaces/recovery")
        assert response.status_code == 401
        assert response.json()["detail"] == "Authentication required"
    get_settings.cache_clear()
