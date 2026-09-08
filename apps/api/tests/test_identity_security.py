from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.core.config import get_settings
from app.routes.auth import router as auth_router
from app.routes.health import router as health_router
from app.security.auth import (
    BOOTSTRAP_USER_ID,
    DUMMY_PASSWORD_HASH,
    SESSION_COOKIE,
    AuthMiddleware,
    create_session_token,
    hash_password,
    verify_password,
)
from app.security.identity_store import (
    AuthUser,
    IdentityAuthorizationError,
    IdentityConflictError,
    IdentityStoreError,
    IdentityValidationError,
)
from app.security.redis_controls import configure_auth_state_client
from tests.fake_redis import FakeAsyncRedis

OWNER_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
DEV_A_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
DEV_B_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
ORG_ADMIN_ID = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
OTHER_ORG_ADMIN_ID = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"


@dataclass
class _StoredUser:
    user: AuthUser
    password_hash: str | None = None


class FakeIdentityStore:
    def __init__(self) -> None:
        self.users: dict[str, _StoredUser] = {}
        self.invitations: dict[str, dict[str, Any]] = {}
        self.fail_closed = False

    def _check(self) -> None:
        if self.fail_closed:
            raise IdentityStoreError("Identity store unavailable")

    def seed(
        self,
        *,
        user_id: str,
        email: str,
        organization_id: str,
        role: str,
        password: str,
        status: str = "active",
        session_version: int = 0,
    ) -> AuthUser:
        now = datetime.now(timezone.utc)
        user = AuthUser(
            user_id=user_id,
            email=email.casefold(),
            organization_id=organization_id,
            role=role,
            status=status,
            session_version=session_version,
            created_by_user_id=None,
            created_at=now,
            updated_at=now,
            activated_at=now if status == "active" else None,
            disabled_at=None,
            password_hash=None,
        )
        self.users[user_id] = _StoredUser(
            user=user,
            password_hash=hash_password(password, salt=b"x" * 16) if status == "active" else None,
        )
        return user

    def get_user_by_id(self, user_id: str, *, include_hash: bool = False) -> AuthUser | None:
        self._check()
        stored = self.users.get(user_id)
        if stored is None:
            return None
        return replace(stored.user, password_hash=stored.password_hash if include_hash else None)

    def get_user_by_email(self, email: str, *, include_hash: bool = False) -> AuthUser | None:
        self._check()
        needle = email.casefold().strip()
        for stored in self.users.values():
            if stored.user.email == needle:
                return replace(stored.user, password_hash=stored.password_hash if include_hash else None)
        return None

    def list_users(self, *, organization_id: str | None = None) -> list[AuthUser]:
        self._check()
        users = [stored.user for stored in self.users.values()]
        if organization_id is not None:
            users = [user for user in users if user.organization_id == organization_id]
        return users

    def count_active_platform_owners(self, *, exclude_user_id: str | None = None) -> int:
        self._check()
        count = 0
        for stored in self.users.values():
            if stored.user.role != "platform_owner" or stored.user.status != "active":
                continue
            if exclude_user_id and stored.user.user_id == exclude_user_id:
                continue
            count += 1
        return count

    def create_invitation(
        self,
        *,
        email: str,
        organization_id: str,
        role: str,
        created_by_user_id: str,
    ) -> tuple[AuthUser, str]:
        self._check()
        actor = self.get_user_by_id(created_by_user_id)
        if actor is None or actor.status != "active" or actor.role not in {"platform_owner", "org_admin"}:
            raise IdentityAuthorizationError("Actor is not authorized")
        if actor.role == "org_admin":
            if organization_id != actor.organization_id or role == "platform_owner":
                raise IdentityAuthorizationError("Actor is not authorized")
        normalized = email.casefold().strip()
        existing = self.get_user_by_email(normalized)
        if actor.role == "org_admin" and existing is not None:
            if existing.organization_id != actor.organization_id or existing.role == "platform_owner":
                raise IdentityAuthorizationError("Actor is not authorized")
        if existing and existing.status == "active":
            raise IdentityConflictError("User already active")
        if existing and existing.status == "disabled":
            raise IdentityConflictError("User is disabled")
        now = datetime.now(timezone.utc)
        if existing:
            user_id = existing.user_id
            stored = self.users[user_id]
            stored.user = replace(
                stored.user,
                organization_id=organization_id,
                role=role,
                status="pending",
                updated_at=now,
                activated_at=None,
            )
            stored.password_hash = None
            for invite in self.invitations.values():
                if invite["user_id"] == user_id and invite["consumed_at"] is None:
                    invite["consumed_at"] = now
        else:
            user_id = str(uuid4())
            user = AuthUser(
                user_id=user_id,
                email=normalized,
                organization_id=organization_id,
                role=role,
                status="pending",
                session_version=0,
                created_by_user_id=created_by_user_id,
                created_at=now,
                updated_at=now,
                activated_at=None,
                disabled_at=None,
            )
            self.users[user_id] = _StoredUser(user=user, password_hash=None)
        token = f"invite-token-{user_id}"
        self.invitations[token] = {
            "user_id": user_id,
            "consumed_at": None,
            "expires_at": datetime(2099, 1, 1, tzinfo=timezone.utc),
        }
        return self.users[user_id].user, token

    def activate_invitation(self, *, token: str, password_hash: str) -> AuthUser:
        self._check()
        invite = self.invitations.get(token)
        if invite is None or invite["consumed_at"] is not None:
            raise IdentityValidationError("Invitation is invalid")
        stored = self.users.get(invite["user_id"])
        if stored is None or stored.user.status != "pending":
            raise IdentityValidationError("Invitation is invalid")
        now = datetime.now(timezone.utc)
        stored.password_hash = password_hash
        stored.user = replace(stored.user, status="active", activated_at=now, updated_at=now)
        invite["consumed_at"] = now
        return stored.user

    def update_user(
        self,
        *,
        user_id: str,
        role: str | None = None,
        organization_id: str | None = None,
        status: str | None = None,
    ) -> AuthUser:
        self._check()
        stored = self.users.get(user_id)
        if stored is None:
            raise IdentityValidationError("User not found")
        new_role = role if role is not None else stored.user.role
        new_org = organization_id if organization_id is not None else stored.user.organization_id
        new_status = status if status is not None else stored.user.status
        demoting = (
            stored.user.role == "platform_owner"
            and stored.user.status == "active"
            and (new_role != "platform_owner" or new_status != "active")
        )
        if demoting and self.count_active_platform_owners(exclude_user_id=user_id) < 1:
            raise IdentityConflictError("Cannot disable or demote the final active platform owner")
        bump = (
            new_role != stored.user.role
            or new_org != stored.user.organization_id
            or (new_status == "disabled" and stored.user.status != "disabled")
        )
        stored.user = replace(
            stored.user,
            role=new_role,
            organization_id=new_org,
            status=new_status,
            session_version=stored.user.session_version + (1 if bump else 0),
            updated_at=datetime.now(timezone.utc),
            disabled_at=datetime.now(timezone.utc) if new_status == "disabled" else None,
        )
        return stored.user

    def revoke_all_sessions(self, user_id: str) -> AuthUser:
        self._check()
        stored = self.users.get(user_id)
        if stored is None:
            raise IdentityValidationError("User not found")
        stored.user = replace(
            stored.user,
            session_version=stored.user.session_version + 1,
            updated_at=datetime.now(timezone.utc),
        )
        return stored.user


def _patch_identity(monkeypatch, store: FakeIdentityStore) -> None:
    monkeypatch.setattr("app.security.auth.get_user_by_id", store.get_user_by_id)
    monkeypatch.setattr("app.routes.auth.get_user_by_id", store.get_user_by_id)
    monkeypatch.setattr("app.routes.auth.get_user_by_email", store.get_user_by_email)
    monkeypatch.setattr("app.routes.auth.list_users", store.list_users)
    monkeypatch.setattr("app.routes.auth.count_active_platform_owners", store.count_active_platform_owners)
    monkeypatch.setattr("app.routes.auth.create_invitation", store.create_invitation)
    monkeypatch.setattr("app.routes.auth.activate_invitation", store.activate_invitation)
    monkeypatch.setattr("app.routes.auth.update_user", store.update_user)
    monkeypatch.setattr("app.routes.auth.revoke_all_sessions", store.revoke_all_sessions)
    monkeypatch.setattr("app.routes.health.ping_identity_store", lambda: not store.fail_closed)


def _configure_database(monkeypatch, store: FakeIdentityStore) -> FakeAsyncRedis:
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_IDENTITY_MODE", "database")
    monkeypatch.setenv("AUTH_BOOTSTRAP_EMAIL", "")
    monkeypatch.setenv("AUTH_PASSWORD_HASH", "")
    monkeypatch.setenv("AUTH_SESSION_SECRET", "s" * 64)
    monkeypatch.setenv("AUTH_ORGANIZATION_ID", "inzozi-digital")
    monkeypatch.setenv("AUTH_SESSION_TTL_MINUTES", "60")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    monkeypatch.setenv("AUTH_TRUSTED_PROXIES", "")
    monkeypatch.setenv("REDIS_URL", "redis://fake-redis/0")
    get_settings.cache_clear()
    fake = FakeAsyncRedis()
    configure_auth_state_client(fake)
    _patch_identity(monkeypatch, store)
    return fake


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(AuthMiddleware)
    app.include_router(auth_router)
    app.include_router(health_router)

    @app.get("/v1/workspaces/runtime")
    def read_workspace() -> dict:
        return {"status": "readable"}

    return app


def _login(client: TestClient, email: str, password: str = "a-secure-staging-password") -> None:
    response = client.post("/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text


def test_database_login_and_activation_flow(monkeypatch):
    store = FakeIdentityStore()
    store.seed(
        user_id=OWNER_ID,
        email="owner@inzozidigital.com",
        organization_id="inzozi-digital",
        role="platform_owner",
        password="a-secure-staging-password",
    )
    _configure_database(monkeypatch, store)
    with TestClient(_app()) as client:
        _login(client, "owner@inzozidigital.com")
        me = client.get("/v1/auth/me").json()
        assert me["user_id"] == OWNER_ID
        assert me["session_version"] == 0

        invited = client.post(
            "/v1/auth/users/invitations",
            json={"email": "dev@inzozidigital.com"},
        )
        assert invited.status_code == 200
        body = invited.json()
        assert body["user"]["role"] == "developer"
        assert "password_hash" not in body["user"]
        assert "invitation_token" in body
        token = body["invitation_token"]
        assert "invite-token-" in token

        client.post("/v1/auth/logout")
        activated = client.post(
            "/v1/auth/activate",
            json={"token": token, "password": "another-secure-password"},
        )
        assert activated.status_code == 200
        assert activated.json()["user"]["status"] == "active"
        assert "password_hash" not in activated.json()["user"]

        replay = client.post(
            "/v1/auth/activate",
            json={"token": token, "password": "another-secure-password"},
        )
        assert replay.status_code == 400

        _login(client, "dev@inzozidigital.com", password="another-secure-password")
        assert client.get("/v1/auth/me").json()["role"] == "developer"
        denied = client.get("/v1/auth/users")
        assert denied.status_code == 403
    get_settings.cache_clear()
    configure_auth_state_client(None)


def test_database_login_uses_dummy_hash_for_unknown_and_pending(monkeypatch):
    store = FakeIdentityStore()
    store.seed(
        user_id=OWNER_ID,
        email="owner@inzozidigital.com",
        organization_id="inzozi-digital",
        role="platform_owner",
        password="a-secure-staging-password",
    )
    pending = store.seed(
        user_id=DEV_A_ID,
        email="pending@inzozidigital.com",
        organization_id="inzozi-digital",
        role="developer",
        password="a-secure-staging-password",
        status="pending",
    )
    store.users[pending.user_id].password_hash = None
    _configure_database(monkeypatch, store)
    seen: list[str] = []

    real_verify = verify_password

    def tracking_verify(password: str, encoded_hash: str) -> bool:
        seen.append(encoded_hash)
        return real_verify(password, encoded_hash)

    monkeypatch.setattr("app.routes.auth.verify_password", tracking_verify)

    with TestClient(_app()) as client:
        unknown = client.post(
            "/v1/auth/login",
            json={"email": "nobody@example.com", "password": "a-secure-staging-password"},
        )
        assert unknown.status_code == 401
        assert unknown.json()["detail"] == "Email or password is incorrect"
        pending_resp = client.post(
            "/v1/auth/login",
            json={"email": "pending@inzozidigital.com", "password": "a-secure-staging-password"},
        )
        assert pending_resp.status_code == 401
        assert pending_resp.json()["detail"] == "Email or password is incorrect"
        wrong = client.post(
            "/v1/auth/login",
            json={"email": "owner@inzozidigital.com", "password": "wrong-password-xx"},
        )
        assert wrong.status_code == 401

    assert DUMMY_PASSWORD_HASH in seen
    get_settings.cache_clear()
    configure_auth_state_client(None)


def test_org_admin_boundaries_and_final_platform_owner(monkeypatch):
    store = FakeIdentityStore()
    store.seed(
        user_id=OWNER_ID,
        email="owner@inzozidigital.com",
        organization_id="inzozi-digital",
        role="platform_owner",
        password="a-secure-staging-password",
    )
    store.seed(
        user_id=ORG_ADMIN_ID,
        email="admin@inzozidigital.com",
        organization_id="inzozi-digital",
        role="org_admin",
        password="a-secure-staging-password",
    )
    store.seed(
        user_id=OTHER_ORG_ADMIN_ID,
        email="admin@other.org",
        organization_id="other-org",
        role="org_admin",
        password="a-secure-staging-password",
    )
    store.seed(
        user_id=DEV_A_ID,
        email="deva@inzozidigital.com",
        organization_id="inzozi-digital",
        role="developer",
        password="a-secure-staging-password",
    )
    _configure_database(monkeypatch, store)

    with TestClient(_app()) as client:
        _login(client, "admin@inzozidigital.com")
        listed = client.get("/v1/auth/users")
        assert listed.status_code == 200
        emails = {user["email"] for user in listed.json()["users"]}
        assert "admin@other.org" not in emails
        assert "deva@inzozidigital.com" in emails

        cross = client.post(
            "/v1/auth/users/invitations",
            json={"email": "x@other.org", "organization_id": "other-org"},
        )
        assert cross.status_code == 403

        promote = client.patch(f"/v1/auth/users/{DEV_A_ID}", json={"role": "platform_owner"})
        assert promote.status_code == 403

        manage_owner = client.patch(f"/v1/auth/users/{OWNER_ID}", json={"status": "disabled"})
        assert manage_owner.status_code == 403

        client.post("/v1/auth/logout")
        _login(client, "owner@inzozidigital.com")
        final = client.patch(f"/v1/auth/users/{OWNER_ID}", json={"status": "disabled"})
        assert final.status_code == 409

        second = client.post(
            "/v1/auth/users/invitations",
            json={"email": "owner2@inzozidigital.com", "role": "platform_owner"},
        )
        assert second.status_code == 200
        token = second.json()["invitation_token"]
        client.post("/v1/auth/logout")
        assert (
            client.post(
                "/v1/auth/activate",
                json={"token": token, "password": "second-owner-pass"},
            ).status_code
            == 200
        )
        _login(client, "owner@inzozidigital.com")
        ok = client.patch(f"/v1/auth/users/{OWNER_ID}", json={"role": "org_admin"})
        assert ok.status_code == 200
        assert ok.json()["user"]["session_version"] == 1
    get_settings.cache_clear()
    configure_auth_state_client(None)


def test_session_version_invalidation_paths(monkeypatch):
    store = FakeIdentityStore()
    store.seed(
        user_id=OWNER_ID,
        email="owner@inzozidigital.com",
        organization_id="inzozi-digital",
        role="platform_owner",
        password="a-secure-staging-password",
    )
    store.seed(
        user_id=DEV_A_ID,
        email="deva@inzozidigital.com",
        organization_id="inzozi-digital",
        role="developer",
        password="a-secure-staging-password",
    )
    store.seed(
        user_id=DEV_B_ID,
        email="devb@inzozidigital.com",
        organization_id="inzozi-digital",
        role="developer",
        password="a-secure-staging-password",
    )
    _configure_database(monkeypatch, store)

    def login_cookie(client: TestClient, email: str) -> str:
        client.cookies.clear()
        response = client.post(
            "/v1/auth/login",
            json={"email": email, "password": "a-secure-staging-password"},
        )
        assert response.status_code == 200
        token = response.cookies.get(SESSION_COOKIE)
        assert token
        return token

    with TestClient(_app()) as client:
        cookie_a = login_cookie(client, "deva@inzozidigital.com")
        login_cookie(client, "owner@inzozidigital.com")
        patched = client.patch(f"/v1/auth/users/{DEV_A_ID}", json={"role": "reviewer"})
        assert patched.status_code == 200
        assert patched.json()["user"]["session_version"] == 1
        client.cookies.clear()
        client.cookies.set(SESSION_COOKIE, cookie_a)
        assert client.get("/v1/auth/me").status_code == 401

        cookie_b = login_cookie(client, "devb@inzozidigital.com")
        login_cookie(client, "owner@inzozidigital.com")
        revoked = client.post(f"/v1/auth/users/{DEV_B_ID}/revoke-sessions")
        assert revoked.status_code == 200
        assert revoked.json()["user"]["session_version"] == 1
        client.cookies.clear()
        client.cookies.set(SESSION_COOKIE, cookie_b)
        assert client.get("/v1/auth/me").status_code == 401
    get_settings.cache_clear()
    configure_auth_state_client(None)


def test_identity_store_outage_returns_503(monkeypatch):
    store = FakeIdentityStore()
    store.seed(
        user_id=OWNER_ID,
        email="owner@inzozidigital.com",
        organization_id="inzozi-digital",
        role="platform_owner",
        password="a-secure-staging-password",
    )
    fake = _configure_database(monkeypatch, store)
    with TestClient(_app()) as client:
        _login(client, "owner@inzozidigital.com")
        store.fail_closed = True
        response = client.get("/v1/auth/me")
        assert response.status_code == 503
        assert response.json()["detail"] == "Identity store unavailable"

        ready = client.get("/ready")
        assert ready.status_code == 503
        assert ready.json()["identity_store"] == "unavailable"
        assert ready.json()["auth_state_store"] == "ready"

        store.fail_closed = False
        fake.fail_closed = True
        redis_down = client.get("/v1/auth/me")
        assert redis_down.status_code == 503
    get_settings.cache_clear()
    configure_auth_state_client(None)


def test_bootstrap_mode_still_issues_bootstrap_user_id(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_IDENTITY_MODE", "bootstrap")
    monkeypatch.setenv("AUTH_BOOTSTRAP_EMAIL", "owner@inzozidigital.com")
    monkeypatch.setenv("AUTH_PASSWORD_HASH", hash_password("a-secure-staging-password", salt=b"1" * 16))
    monkeypatch.setenv("AUTH_SESSION_SECRET", "s" * 64)
    monkeypatch.setenv("AUTH_BOOTSTRAP_ROLE", "platform_owner")
    monkeypatch.setenv("AUTH_ORGANIZATION_ID", "inzozi-digital")
    monkeypatch.setenv("AUTH_SESSION_TTL_MINUTES", "60")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    get_settings.cache_clear()
    configure_auth_state_client(FakeAsyncRedis())
    with TestClient(_app()) as client:
        login = client.post(
            "/v1/auth/login",
            json={"email": "owner@inzozidigital.com", "password": "a-secure-staging-password"},
        )
        assert login.status_code == 200
        assert login.json()["user_id"] == BOOTSTRAP_USER_ID
        assert client.get("/ready").json()["identity_store"] == "disabled"
        # Bootstrap credentials must not work when mode is switched without durable users.
    get_settings.cache_clear()
    configure_auth_state_client(None)


def test_database_mode_never_uses_bootstrap_credentials(monkeypatch):
    store = FakeIdentityStore()
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_IDENTITY_MODE", "database")
    monkeypatch.setenv("AUTH_BOOTSTRAP_EMAIL", "owner@inzozidigital.com")
    monkeypatch.setenv("AUTH_PASSWORD_HASH", hash_password("a-secure-staging-password", salt=b"1" * 16))
    monkeypatch.setenv("AUTH_SESSION_SECRET", "s" * 64)
    monkeypatch.setenv("AUTH_ORGANIZATION_ID", "inzozi-digital")
    get_settings.cache_clear()
    configure_auth_state_client(FakeAsyncRedis())
    _patch_identity(monkeypatch, store)
    with TestClient(_app()) as client:
        response = client.post(
            "/v1/auth/login",
            json={"email": "owner@inzozidigital.com", "password": "a-secure-staging-password"},
        )
        assert response.status_code == 401
    get_settings.cache_clear()
    configure_auth_state_client(None)


def test_session_token_requires_uid_and_sv(monkeypatch):
    monkeypatch.setenv("AUTH_SESSION_SECRET", "s" * 64)
    get_settings.cache_clear()
    token, _ = create_session_token(
        "owner@inzozidigital.com",
        "platform_owner",
        "inzozi-digital",
        user_id=OWNER_ID,
        session_version=3,
    )
    from app.security.auth import decode_session_token

    principal = decode_session_token(token)
    assert principal.user_id == OWNER_ID
    assert principal.session_version == 3
    get_settings.cache_clear()
