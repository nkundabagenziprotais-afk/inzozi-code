from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from app.core.config import get_settings
from app.security.auth import AuthPrincipal, ROLE_PERMISSIONS, hash_password
from app.security.identity_store import (
    IdentityConflictError,
    IdentityValidationError,
    activate_invitation,
    create_invitation,
    ensure_identity_schema,
    get_user_by_email,
    invitation_token_digest,
    revoke_all_sessions,
    seed_platform_owner,
    update_user,
)
from app.security.workspace_ownership import (
    ensure_workspace_ownership_schema,
    list_accessible_active_workspaces,
    principal_can_access,
    record_workspace_audit,
    register_workspace,
)

RUN_POSTGRES = os.getenv("RUN_POSTGRES_INTEGRATION") == "1"
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://inzozi_code:test-only@127.0.0.1:5432/inzozi_code")

pytestmark = pytest.mark.skipif(
    not RUN_POSTGRES,
    reason="Set RUN_POSTGRES_INTEGRATION=1 with Postgres available",
)


@pytest.fixture(autouse=True)
def _configure_database(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)
    monkeypatch.setenv("AUTH_IDENTITY_MODE", "database")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_SESSION_SECRET", "p" * 64)
    get_settings.cache_clear()
    ensure_identity_schema()
    ensure_workspace_ownership_schema()
    with psycopg.connect(DATABASE_URL, autocommit=True) as connection, connection.cursor() as cursor:
        cursor.execute("TRUNCATE auth_invitations, auth_users, workspace_access_audit, workspace_ownership RESTART IDENTITY CASCADE")
    yield
    get_settings.cache_clear()


def _principal(**kwargs) -> AuthPrincipal:
    defaults = {
        "email": "owner@inzozidigital.com",
        "role": "platform_owner",
        "organization_id": "inzozi-digital",
        "permissions": ROLE_PERMISSIONS["platform_owner"],
        "session_id": "test",
        "expires_at": None,
        "user_id": str(uuid4()),
        "session_version": 0,
        "auth_enabled": True,
    }
    defaults.update(kwargs)
    return AuthPrincipal(**defaults)


def test_email_uniqueness_and_invitation_digest_only():
    password_hash = hash_password("a-secure-staging-password", salt=b"p" * 16)
    owner = seed_platform_owner(
        email="Owner@InzoziDigital.com",
        password_hash=password_hash,
        organization_id="inzozi-digital",
    )
    assert owner.email == "owner@inzozidigital.com"
    again = seed_platform_owner(
        email="owner@inzozidigital.com",
        password_hash=password_hash,
        organization_id="inzozi-digital",
    )
    assert again.user_id == owner.user_id

    with pytest.raises(IdentityConflictError):
        seed_platform_owner(
            email="owner@inzozidigital.com",
            password_hash=hash_password("different-password-xx", salt=b"q" * 16),
            organization_id="inzozi-digital",
        )

    user, token = create_invitation(
        email="Dev@InzoziDigital.com",
        organization_id="inzozi-digital",
        role="developer",
        created_by_user_id=owner.user_id,
    )
    assert user.role == "developer"
    assert user.status == "pending"
    digest = invitation_token_digest(token)
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT token_digest, password_hash FROM auth_invitations i JOIN auth_users u ON u.user_id = i.user_id")
        row = cursor.fetchone()
        assert row["token_digest"] == digest
        assert token not in str(row)
        assert row["password_hash"] is None
        cursor.execute("SELECT password_hash FROM auth_users WHERE user_id = %s", (user.user_id,))
        assert cursor.fetchone()["password_hash"] is None


def test_invitation_expiry_replay_and_reissue():
    owner = seed_platform_owner(
        email="owner@inzozidigital.com",
        password_hash=hash_password("a-secure-staging-password", salt=b"p" * 16),
        organization_id="inzozi-digital",
    )
    user, token = create_invitation(
        email="dev@inzozidigital.com",
        organization_id="inzozi-digital",
        role="developer",
        created_by_user_id=owner.user_id,
    )
    password_hash = hash_password("activated-password", salt=b"a" * 16)
    activated = activate_invitation(token=token, password_hash=password_hash)
    assert activated.status == "active"
    public = activated.public_dict()
    assert "password_hash" not in public

    with pytest.raises(IdentityValidationError):
        activate_invitation(token=token, password_hash=password_hash)

    # Reissue invalidates prior unconsumed invites for pending users.
    pending_user, first = create_invitation(
        email="pending@inzozidigital.com",
        organization_id="inzozi-digital",
        role="developer",
        created_by_user_id=owner.user_id,
    )
    _, second = create_invitation(
        email="pending@inzozidigital.com",
        organization_id="inzozi-digital",
        role="developer",
        created_by_user_id=owner.user_id,
    )
    with pytest.raises(IdentityValidationError):
        activate_invitation(token=first, password_hash=password_hash)
    activate_invitation(token=second, password_hash=password_hash)
    assert get_user_by_email("pending@inzozidigital.com").status == "active"

    # Force-expire a fresh invite.
    _, expired_token = create_invitation(
        email="late@inzozidigital.com",
        organization_id="inzozi-digital",
        role="developer",
        created_by_user_id=owner.user_id,
    )
    with psycopg.connect(DATABASE_URL, autocommit=True) as connection, connection.cursor() as cursor:
        cursor.execute(
            "UPDATE auth_invitations SET expires_at = %s WHERE token_digest = %s",
            (datetime.now(timezone.utc) - timedelta(minutes=1), invitation_token_digest(expired_token)),
        )
    with pytest.raises(IdentityValidationError):
        activate_invitation(token=expired_token, password_hash=password_hash)
    assert pending_user.role == "developer"


def test_session_version_and_workspace_user_id_correlation():
    owner = seed_platform_owner(
        email="owner@inzozidigital.com",
        password_hash=hash_password("a-secure-staging-password", salt=b"p" * 16),
        organization_id="inzozi-digital",
    )
    _, token = create_invitation(
        email="deva@inzozidigital.com",
        organization_id="inzozi-digital",
        role="developer",
        created_by_user_id=owner.user_id,
    )
    developer = activate_invitation(
        token=token,
        password_hash=hash_password("developer-password", salt=b"d" * 16),
    )
    assert developer.session_version == 0
    bumped = update_user(user_id=developer.user_id, role="reviewer")
    assert bumped.session_version == 1
    revoked = revoke_all_sessions(developer.user_id)
    assert revoked.session_version == 2

    principal = _principal(
        email=developer.email,
        role="developer",
        user_id=developer.user_id,
        organization_id="inzozi-digital",
        permissions=ROLE_PERMISSIONS["developer"],
    )
    ownership = register_workspace(
        workspace_id="a" * 32,
        principal=principal,
        repository_url="https://github.com/example/private-repo",
    )
    assert ownership.owner_user_id == developer.user_id

    other = _principal(
        email="devb@inzozidigital.com",
        role="developer",
        user_id=str(uuid4()),
        organization_id="inzozi-digital",
        permissions=ROLE_PERMISSIONS["developer"],
    )
    assert principal_can_access(principal, ownership) is True
    assert principal_can_access(other, ownership) is False

    org_admin = _principal(
        email="admin@inzozidigital.com",
        role="org_admin",
        user_id=str(uuid4()),
        organization_id="inzozi-digital",
        permissions=ROLE_PERMISSIONS["org_admin"],
    )
    assert principal_can_access(org_admin, ownership) is True

    other_org_admin = _principal(
        email="admin@other.org",
        role="org_admin",
        user_id=str(uuid4()),
        organization_id="other-org",
        permissions=ROLE_PERMISSIONS["org_admin"],
    )
    assert principal_can_access(other_org_admin, ownership) is False

    accessible = list_accessible_active_workspaces(principal)
    assert [item.workspace_id for item in accessible] == ["a" * 32]
    assert list_accessible_active_workspaces(other) == []

    record_workspace_audit(
        principal=principal,
        event_type="workspace.create",
        outcome="allowed",
        request_id="req-1",
        workspace_id="a" * 32,
    )
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT actor_user_id, actor_email FROM workspace_access_audit LIMIT 1")
        row = cursor.fetchone()
        assert str(row["actor_user_id"]) == developer.user_id
        assert row["actor_email"] == developer.email

    # Legacy NULL owner_user_id remains email-scoped.
    with psycopg.connect(DATABASE_URL, autocommit=True) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO workspace_ownership (
                workspace_id, organization_id, owner_email, owner_role, owner_user_id,
                repository_url, namespace
            ) VALUES (%s, %s, %s, %s, NULL, %s, %s)
            """,
            ("b" * 32, "inzozi-digital", developer.email, "developer", "https://example.com/r", "n" * 32),
        )
    legacy = list_accessible_active_workspaces(principal)
    assert {item.workspace_id for item in legacy} == {"a" * 32, "b" * 32}


def test_org_admin_cannot_reissue_cross_org_or_platform_owner_pending():
    from app.security.identity_store import IdentityAuthorizationError

    owner = seed_platform_owner(
        email="owner@inzozidigital.com",
        password_hash=hash_password("a-secure-staging-password", salt=b"p" * 16),
        organization_id="org-a",
    )
    # Create org A admin via owner invitation + activation.
    _, admin_token = create_invitation(
        email="admin-a@inzozidigital.com",
        organization_id="org-a",
        role="org_admin",
        created_by_user_id=owner.user_id,
    )
    admin_a = activate_invitation(
        token=admin_token,
        password_hash=hash_password("admin-a-password", salt=b"a" * 16),
    )

    # Org B pending developer created by platform owner.
    pending_b, _ = create_invitation(
        email="pending-b@inzozidigital.com",
        organization_id="org-b",
        role="developer",
        created_by_user_id=owner.user_id,
    )
    assert pending_b.organization_id == "org-b"

    with pytest.raises(IdentityAuthorizationError):
        create_invitation(
            email="pending-b@inzozidigital.com",
            organization_id="org-a",
            role="developer",
            created_by_user_id=admin_a.user_id,
        )
    with pytest.raises(IdentityAuthorizationError):
        create_invitation(
            email="pending-b@inzozidigital.com",
            organization_id="org-b",
            role="developer",
            created_by_user_id=admin_a.user_id,
        )

    # Pending platform_owner cannot be reissued/demoted by org_admin.
    pending_owner, _ = create_invitation(
        email="pending-owner@inzozidigital.com",
        organization_id="org-a",
        role="platform_owner",
        created_by_user_id=owner.user_id,
    )
    with pytest.raises(IdentityAuthorizationError):
        create_invitation(
            email="pending-owner@inzozidigital.com",
            organization_id="org-a",
            role="developer",
            created_by_user_id=admin_a.user_id,
        )
    with pytest.raises(IdentityAuthorizationError):
        create_invitation(
            email="pending-owner@inzozidigital.com",
            organization_id="org-a",
            role="platform_owner",
            created_by_user_id=admin_a.user_id,
        )

    # Same-org pending developer reissue by org_admin succeeds.
    pending_dev, first_token = create_invitation(
        email="pending-dev@inzozidigital.com",
        organization_id="org-a",
        role="developer",
        created_by_user_id=admin_a.user_id,
    )
    reissued, second_token = create_invitation(
        email="pending-dev@inzozidigital.com",
        organization_id="org-a",
        role="developer",
        created_by_user_id=admin_a.user_id,
    )
    assert reissued.user_id == pending_dev.user_id
    assert first_token != second_token

    # Platform owner authorized reissue across orgs succeeds.
    reissued_b, _ = create_invitation(
        email="pending-b@inzozidigital.com",
        organization_id="org-b",
        role="developer",
        created_by_user_id=owner.user_id,
    )
    assert reissued_b.user_id == pending_b.user_id
    assert reissued_b.organization_id == "org-b"
    assert pending_owner.role == "platform_owner"


def test_concurrent_platform_owner_demotion_leaves_one_active():
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from app.security.identity_store import IdentityConflictError

    owner_hash = hash_password("a-secure-staging-password", salt=b"p" * 16)
    first = seed_platform_owner(
        email="owner1@inzozidigital.com",
        password_hash=owner_hash,
        organization_id="inzozi-digital",
    )
    # Seed second owner via direct insert through invitation activation.
    _, token = create_invitation(
        email="owner2@inzozidigital.com",
        organization_id="inzozi-digital",
        role="platform_owner",
        created_by_user_id=first.user_id,
    )
    second = activate_invitation(
        token=token,
        password_hash=hash_password("second-owner-password", salt=b"s" * 16),
    )

    results: list[str] = []

    def demote(user_id: str) -> str:
        try:
            update_user(user_id=user_id, role="org_admin")
            return "ok"
        except IdentityConflictError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(demote, first.user_id),
            pool.submit(demote, second.user_id),
        ]
        for future in as_completed(futures):
            results.append(future.result())

    assert results.count("ok") == 1
    assert results.count("conflict") == 1
    remaining = 0
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) AS count FROM auth_users WHERE role = 'platform_owner' AND status = 'active'"
        )
        remaining = int(cursor.fetchone()["count"])
    assert remaining >= 1


def test_any_status_transition_increments_session_version():
    owner = seed_platform_owner(
        email="owner@inzozidigital.com",
        password_hash=hash_password("a-secure-staging-password", salt=b"p" * 16),
        organization_id="inzozi-digital",
    )
    _, token = create_invitation(
        email="deva@inzozidigital.com",
        organization_id="inzozi-digital",
        role="developer",
        created_by_user_id=owner.user_id,
    )
    developer = activate_invitation(
        token=token,
        password_hash=hash_password("developer-password", salt=b"d" * 16),
    )
    before = developer.session_version
    assert developer.status == "active"

    pending = update_user(user_id=developer.user_id, status="pending")
    assert pending.status == "pending"
    assert pending.session_version == before + 1

    active_again = update_user(user_id=developer.user_id, status="active")
    assert active_again.status == "active"
    assert active_again.session_version == before + 2
    assert active_again.session_version > before
