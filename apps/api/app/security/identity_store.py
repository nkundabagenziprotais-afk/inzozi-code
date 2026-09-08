from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import secrets
from typing import Any
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from app.core.config import get_settings

INVITATION_TTL = timedelta(days=7)
USER_STATUSES = frozenset({"pending", "active", "disabled"})
PUBLIC_USER_COLUMNS = (
    "user_id",
    "email",
    "organization_id",
    "role",
    "status",
    "session_version",
    "created_by_user_id",
    "created_at",
    "updated_at",
    "activated_at",
    "disabled_at",
)


class IdentityStoreError(RuntimeError):
    """Identity persistence failed or is unavailable."""


class IdentityConflictError(IdentityStoreError):
    """Requested identity mutation conflicts with durable state."""


class IdentityValidationError(IdentityStoreError):
    """Caller supplied an invalid identity mutation."""


@dataclass(frozen=True)
class AuthUser:
    user_id: str
    email: str
    organization_id: str
    role: str
    status: str
    session_version: int
    created_by_user_id: str | None
    created_at: datetime
    updated_at: datetime
    activated_at: datetime | None
    disabled_at: datetime | None
    password_hash: str | None = None

    def public_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "email": self.email,
            "organization_id": self.organization_id,
            "role": self.role,
            "status": self.status,
            "session_version": self.session_version,
            "created_by_user_id": self.created_by_user_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "activated_at": self.activated_at.isoformat() if self.activated_at else None,
            "disabled_at": self.disabled_at.isoformat() if self.disabled_at else None,
        }


def normalize_email(email: str) -> str:
    return email.casefold().strip()


def invitation_token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _connect(*, autocommit: bool = True):
    try:
        return psycopg.connect(get_settings().database_url, autocommit=autocommit, row_factory=dict_row)
    except Exception as exc:  # noqa: BLE001 - fail closed for any store outage
        raise IdentityStoreError("Identity store unavailable") from exc


def ensure_identity_schema() -> None:
    with _connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS auth_users (
                user_id UUID PRIMARY KEY,
                email VARCHAR(191) NOT NULL,
                organization_id VARCHAR(100) NOT NULL,
                role VARCHAR(50) NOT NULL,
                password_hash TEXT NULL,
                status VARCHAR(20) NOT NULL,
                session_version INTEGER NOT NULL DEFAULT 0,
                created_by_user_id UUID NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                activated_at TIMESTAMPTZ NULL,
                disabled_at TIMESTAMPTZ NULL,
                CONSTRAINT auth_users_email_unique UNIQUE (email),
                CONSTRAINT auth_users_status_check CHECK (status IN ('pending', 'active', 'disabled')),
                CONSTRAINT auth_users_session_version_nonneg CHECK (session_version >= 0)
            )
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS auth_users_org_status_idx
            ON auth_users (organization_id, status)
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS auth_invitations (
                invite_id UUID PRIMARY KEY,
                user_id UUID NOT NULL REFERENCES auth_users(user_id) ON DELETE CASCADE,
                token_digest CHAR(64) NOT NULL,
                expires_at TIMESTAMPTZ NOT NULL,
                consumed_at TIMESTAMPTZ NULL,
                created_by_user_id UUID NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT auth_invitations_token_digest_unique UNIQUE (token_digest)
            )
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS auth_invitations_user_active_idx
            ON auth_invitations (user_id)
            WHERE consumed_at IS NULL
            """
        )


def ping_identity_store() -> bool:
    try:
        with _connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return True
    except IdentityStoreError:
        return False
    except Exception:  # noqa: BLE001
        return False


def _row_to_user(row: dict[str, Any], *, include_hash: bool = False) -> AuthUser:
    return AuthUser(
        user_id=str(row["user_id"]),
        email=str(row["email"]),
        organization_id=str(row["organization_id"]),
        role=str(row["role"]),
        status=str(row["status"]),
        session_version=int(row["session_version"]),
        created_by_user_id=str(row["created_by_user_id"]) if row.get("created_by_user_id") else None,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        activated_at=row.get("activated_at"),
        disabled_at=row.get("disabled_at"),
        password_hash=str(row["password_hash"]) if include_hash and row.get("password_hash") else None,
    )


def get_user_by_id(user_id: str, *, include_hash: bool = False) -> AuthUser | None:
    columns = ", ".join(PUBLIC_USER_COLUMNS + (("password_hash",) if include_hash else ()))
    try:
        with _connect() as connection, connection.cursor() as cursor:
            cursor.execute(f"SELECT {columns} FROM auth_users WHERE user_id = %s", (user_id,))
            row = cursor.fetchone()
    except IdentityStoreError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise IdentityStoreError("Identity store unavailable") from exc
    return _row_to_user(row, include_hash=include_hash) if row else None


def get_user_by_email(email: str, *, include_hash: bool = False) -> AuthUser | None:
    columns = ", ".join(PUBLIC_USER_COLUMNS + (("password_hash",) if include_hash else ()))
    try:
        with _connect() as connection, connection.cursor() as cursor:
            cursor.execute(f"SELECT {columns} FROM auth_users WHERE email = %s", (normalize_email(email),))
            row = cursor.fetchone()
    except IdentityStoreError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise IdentityStoreError("Identity store unavailable") from exc
    return _row_to_user(row, include_hash=include_hash) if row else None


def count_active_platform_owners(*, exclude_user_id: str | None = None) -> int:
    try:
        with _connect() as connection, connection.cursor() as cursor:
            if exclude_user_id:
                cursor.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM auth_users
                    WHERE role = 'platform_owner' AND status = 'active' AND user_id <> %s
                    """,
                    (exclude_user_id,),
                )
            else:
                cursor.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM auth_users
                    WHERE role = 'platform_owner' AND status = 'active'
                    """
                )
            row = cursor.fetchone()
    except IdentityStoreError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise IdentityStoreError("Identity store unavailable") from exc
    return int(row["count"]) if row else 0


def list_users(*, organization_id: str | None = None) -> list[AuthUser]:
    try:
        with _connect() as connection, connection.cursor() as cursor:
            columns = ", ".join(PUBLIC_USER_COLUMNS)
            if organization_id is None:
                cursor.execute(f"SELECT {columns} FROM auth_users ORDER BY created_at ASC")
            else:
                cursor.execute(
                    f"SELECT {columns} FROM auth_users WHERE organization_id = %s ORDER BY created_at ASC",
                    (organization_id,),
                )
            rows = cursor.fetchall()
    except IdentityStoreError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise IdentityStoreError("Identity store unavailable") from exc
    return [_row_to_user(row) for row in rows]


def seed_platform_owner(
    *,
    email: str,
    password_hash: str,
    organization_id: str,
    role: str = "platform_owner",
) -> AuthUser:
    if role != "platform_owner":
        raise IdentityValidationError("Seed helper may only create the platform owner")
    normalized = normalize_email(email)
    existing = get_user_by_email(normalized, include_hash=True)
    if existing is not None:
        if (
            existing.role != role
            or existing.organization_id != organization_id
            or existing.status != "active"
            or existing.password_hash != password_hash
        ):
            raise IdentityConflictError("Conflicting durable identity already exists for bootstrap email")
        return existing

    user_id = str(uuid4())
    try:
        with _connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO auth_users (
                    user_id, email, organization_id, role, password_hash, status,
                    session_version, created_by_user_id, activated_at
                ) VALUES (%s, %s, %s, %s, %s, 'active', 0, NULL, NOW())
                RETURNING user_id, email, organization_id, role, status, session_version,
                          created_by_user_id, created_at, updated_at, activated_at, disabled_at
                """,
                (user_id, normalized, organization_id, role, password_hash),
            )
            row = cursor.fetchone()
    except IdentityStoreError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise IdentityStoreError("Identity store unavailable") from exc
    assert row is not None
    return _row_to_user(row)


def create_invitation(
    *,
    email: str,
    organization_id: str,
    role: str,
    created_by_user_id: str,
) -> tuple[AuthUser, str]:
    """Create or reissue a pending invitation. Returns (user, plaintext_token)."""
    normalized = normalize_email(email)
    token = secrets.token_urlsafe(32)
    digest = invitation_token_digest(token)
    expires_at = datetime.now(timezone.utc) + INVITATION_TTL
    invite_id = str(uuid4())

    try:
        with _connect(autocommit=False) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT user_id, status FROM auth_users WHERE email = %s FOR UPDATE",
                    (normalized,),
                )
                existing = cursor.fetchone()
                if existing and existing["status"] == "active":
                    raise IdentityConflictError("User already active")
                if existing and existing["status"] == "disabled":
                    raise IdentityConflictError("User is disabled")

                if existing:
                    user_id = str(existing["user_id"])
                    cursor.execute(
                        """
                        UPDATE auth_users
                        SET organization_id = %s, role = %s, status = 'pending',
                            password_hash = NULL, updated_at = NOW(),
                            activated_at = NULL, disabled_at = NULL
                        WHERE user_id = %s
                        """,
                        (organization_id, role, user_id),
                    )
                    cursor.execute(
                        """
                        UPDATE auth_invitations
                        SET consumed_at = NOW()
                        WHERE user_id = %s AND consumed_at IS NULL
                        """,
                        (user_id,),
                    )
                else:
                    user_id = str(uuid4())
                    cursor.execute(
                        """
                        INSERT INTO auth_users (
                            user_id, email, organization_id, role, password_hash, status,
                            session_version, created_by_user_id
                        ) VALUES (%s, %s, %s, %s, NULL, 'pending', 0, %s)
                        """,
                        (user_id, normalized, organization_id, role, created_by_user_id),
                    )

                cursor.execute(
                    """
                    INSERT INTO auth_invitations (
                        invite_id, user_id, token_digest, expires_at, created_by_user_id
                    ) VALUES (%s, %s, %s, %s, %s)
                    """,
                    (invite_id, user_id, digest, expires_at, created_by_user_id),
                )
                cursor.execute(
                    """
                    SELECT user_id, email, organization_id, role, status, session_version,
                           created_by_user_id, created_at, updated_at, activated_at, disabled_at
                    FROM auth_users WHERE user_id = %s
                    """,
                    (user_id,),
                )
                row = cursor.fetchone()
            connection.commit()
    except (IdentityConflictError, IdentityValidationError):
        raise
    except IdentityStoreError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise IdentityStoreError("Identity store unavailable") from exc

    assert row is not None
    return _row_to_user(row), token


def activate_invitation(*, token: str, password_hash: str) -> AuthUser:
    digest = invitation_token_digest(token)
    try:
        with _connect(autocommit=False) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT invite_id, user_id, expires_at, consumed_at
                    FROM auth_invitations
                    WHERE token_digest = %s
                    FOR UPDATE
                    """,
                    (digest,),
                )
                invite = cursor.fetchone()
                if invite is None or invite["consumed_at"] is not None:
                    raise IdentityValidationError("Invitation is invalid")
                expires_at = invite["expires_at"]
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
                if expires_at <= datetime.now(timezone.utc):
                    raise IdentityValidationError("Invitation is invalid")

                cursor.execute(
                    """
                    SELECT user_id, status FROM auth_users WHERE user_id = %s FOR UPDATE
                    """,
                    (invite["user_id"],),
                )
                user = cursor.fetchone()
                if user is None or user["status"] != "pending":
                    raise IdentityValidationError("Invitation is invalid")

                cursor.execute(
                    """
                    UPDATE auth_users
                    SET password_hash = %s, status = 'active', activated_at = NOW(),
                        updated_at = NOW(), disabled_at = NULL
                    WHERE user_id = %s
                    """,
                    (password_hash, invite["user_id"]),
                )
                cursor.execute(
                    "UPDATE auth_invitations SET consumed_at = NOW() WHERE invite_id = %s",
                    (invite["invite_id"],),
                )
                cursor.execute(
                    """
                    UPDATE auth_invitations SET consumed_at = NOW()
                    WHERE user_id = %s AND consumed_at IS NULL
                    """,
                    (invite["user_id"],),
                )
                cursor.execute(
                    """
                    SELECT user_id, email, organization_id, role, status, session_version,
                           created_by_user_id, created_at, updated_at, activated_at, disabled_at
                    FROM auth_users WHERE user_id = %s
                    """,
                    (invite["user_id"],),
                )
                row = cursor.fetchone()
            connection.commit()
    except IdentityValidationError:
        raise
    except IdentityStoreError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise IdentityStoreError("Identity store unavailable") from exc
    assert row is not None
    return _row_to_user(row)


def update_user(
    *,
    user_id: str,
    role: str | None = None,
    organization_id: str | None = None,
    status: str | None = None,
) -> AuthUser:
    if status is not None and status not in USER_STATUSES:
        raise IdentityValidationError("Invalid user status")
    existing = get_user_by_id(user_id)
    if existing is None:
        raise IdentityValidationError("User not found")

    new_role = role if role is not None else existing.role
    new_org = organization_id if organization_id is not None else existing.organization_id
    new_status = status if status is not None else existing.status
    bump_session = (
        new_role != existing.role
        or new_org != existing.organization_id
        or (new_status == "disabled" and existing.status != "disabled")
    )

    try:
        with _connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE auth_users
                SET role = %s,
                    organization_id = %s,
                    status = %s,
                    session_version = CASE WHEN %s THEN session_version + 1 ELSE session_version END,
                    updated_at = NOW(),
                    disabled_at = CASE
                        WHEN %s = 'disabled' AND status <> 'disabled' THEN NOW()
                        WHEN %s <> 'disabled' THEN NULL
                        ELSE disabled_at
                    END
                WHERE user_id = %s
                RETURNING user_id, email, organization_id, role, status, session_version,
                          created_by_user_id, created_at, updated_at, activated_at, disabled_at
                """,
                (new_role, new_org, new_status, bump_session, new_status, new_status, user_id),
            )
            row = cursor.fetchone()
    except IdentityStoreError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise IdentityStoreError("Identity store unavailable") from exc
    assert row is not None
    return _row_to_user(row)


def revoke_all_sessions(user_id: str) -> AuthUser:
    existing = get_user_by_id(user_id)
    if existing is None:
        raise IdentityValidationError("User not found")
    try:
        with _connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE auth_users
                SET session_version = session_version + 1, updated_at = NOW()
                WHERE user_id = %s
                RETURNING user_id, email, organization_id, role, status, session_version,
                          created_by_user_id, created_at, updated_at, activated_at, disabled_at
                """,
                (user_id,),
            )
            row = cursor.fetchone()
    except IdentityStoreError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise IdentityStoreError("Identity store unavailable") from exc
    assert row is not None
    return _row_to_user(row)
