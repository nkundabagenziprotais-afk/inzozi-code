from __future__ import annotations

from pathlib import Path

from app.security.auth import DUMMY_PASSWORD_HASH, SESSION_COOKIE


FORBIDDEN_IDENTITY_MARKERS = (
    "AUTH_PASSWORD_HASH",
    "AUTH_SESSION_SECRET",
    "password_hash",
    "invitation_token",
    "inzozi_session",
    DUMMY_PASSWORD_HASH,
    SESSION_COOKIE,
)


def test_workspace_broker_runtime_environment_excludes_identity_secrets():
    """Broker constructs runtime/helper env from workspace fields only — no identity material."""
    broker_main = Path(__file__).resolve().parents[3] / "services/workspace/broker/app/main.py"
    text = broker_main.read_text(encoding="utf-8")
    # Spot-check the runtime container environment block keys that must remain non-identity.
    assert '"WORKSPACE_ROOT"' in text
    assert '"WORKSPACE_MAX_FILE_BYTES"' in text
    for marker in ("AUTH_PASSWORD_HASH", "AUTH_SESSION_SECRET", "invitation_token", "password_hash"):
        assert marker not in text


def test_workspace_manager_does_not_forward_identity_secrets():
    manager_main = Path(__file__).resolve().parents[3] / "services/workspace/manager/app/main.py"
    text = manager_main.read_text(encoding="utf-8")
    for marker in ("AUTH_PASSWORD_HASH", "AUTH_SESSION_SECRET", "invitation_token", "DUMMY_PASSWORD_HASH"):
        assert marker not in text


def test_identity_public_payload_helpers_exclude_secrets():
    from app.security.identity_store import AuthUser
    from datetime import datetime, timezone
    from uuid import uuid4

    user = AuthUser(
        user_id=str(uuid4()),
        email="dev@inzozidigital.com",
        organization_id="inzozi-digital",
        role="developer",
        status="active",
        session_version=0,
        created_by_user_id=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        activated_at=datetime.now(timezone.utc),
        disabled_at=None,
        password_hash="pbkdf2_sha256$600000$should-not-leak$should-not-leak",
    )
    payload = user.public_dict()
    serialized = str(payload)
    assert "password_hash" not in payload
    assert "should-not-leak" not in serialized
    for marker in FORBIDDEN_IDENTITY_MARKERS:
        if marker in {DUMMY_PASSWORD_HASH, SESSION_COOKIE}:
            continue
        assert marker not in serialized
