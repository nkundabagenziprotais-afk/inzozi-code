from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.security.auth import BOOTSTRAP_USER_ID, hash_password
from app.security.identity_store import (
    AuthUser,
    IdentityConflictError,
    IdentityValidationError,
    invitation_token_digest,
    normalize_email,
)
from app.security.workspace_ownership import owner_user_id_from_principal


def test_normalize_email_casefold_and_strip():
    assert normalize_email("  Owner@InzoziDigital.COM ") == "owner@inzozidigital.com"


def test_invitation_token_digest_is_sha256_hex():
    digest = invitation_token_digest("one-time-token")
    assert len(digest) == 64
    assert digest == invitation_token_digest("one-time-token")
    assert digest != invitation_token_digest("other-token")


def test_auth_user_public_dict_excludes_password_hash():
    user = AuthUser(
        user_id=str(uuid4()),
        email="dev@inzozidigital.com",
        organization_id="inzozi-digital",
        role="developer",
        status="active",
        session_version=2,
        created_by_user_id=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        activated_at=datetime.now(timezone.utc),
        disabled_at=None,
        password_hash=hash_password("a-secure-staging-password", salt=b"z" * 16),
    )
    public = user.public_dict()
    assert "password_hash" not in public
    assert public["session_version"] == 2
    assert public["role"] == "developer"


def test_owner_user_id_skips_bootstrap_sentinel():
    class Principal:
        user_id = BOOTSTRAP_USER_ID

    assert owner_user_id_from_principal(Principal()) is None

    class Durable:
        user_id = "11111111-1111-4111-8111-111111111111"

    assert owner_user_id_from_principal(Durable()) == "11111111-1111-4111-8111-111111111111"

    class Development:
        user_id = "development"

    assert owner_user_id_from_principal(Development()) is None


def test_identity_error_hierarchy():
    assert issubclass(IdentityConflictError, Exception)
    assert issubclass(IdentityValidationError, Exception)
    with pytest.raises(IdentityConflictError):
        raise IdentityConflictError("conflict")
