from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import re
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from app.core.config import get_settings
from app.security.auth import BOOTSTRAP_USER_ID

WORKSPACE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
PRIVILEGED_ROLES = frozenset({"platform_owner", "org_admin"})
OWNERSHIP_SELECT_COLUMNS = (
    "workspace_id, organization_id, owner_email, owner_role, owner_user_id, "
    "repository_url, namespace, created_at, deleted_at"
)


@dataclass(frozen=True)
class WorkspaceOwnership:
    workspace_id: str
    organization_id: str
    owner_email: str
    owner_role: str
    repository_url: str
    namespace: str
    created_at: datetime
    deleted_at: datetime | None
    owner_user_id: str | None = None


def workspace_namespace(organization_id: str, owner_email: str) -> str:
    material = f"{organization_id.strip().casefold()}\0{owner_email.strip().casefold()}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()[:32]


def _connect():
    return psycopg.connect(get_settings().database_url, autocommit=True, row_factory=dict_row)


def _as_uuid_or_none(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        return None


def owner_user_id_from_principal(principal) -> str | None:
    """Persist durable user_id when present; bootstrap/development sentinels stay NULL."""
    raw = getattr(principal, "user_id", None)
    parsed = _as_uuid_or_none(raw)
    if parsed is None:
        return None
    if parsed == BOOTSTRAP_USER_ID:
        return None
    return parsed


def actor_user_id_from_principal(principal) -> str | None:
    raw = getattr(principal, "user_id", None)
    if raw == BOOTSTRAP_USER_ID:
        return None
    return _as_uuid_or_none(raw)


def ensure_workspace_ownership_schema() -> None:
    with _connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS workspace_ownership (
                workspace_id CHAR(32) PRIMARY KEY,
                organization_id VARCHAR(100) NOT NULL,
                owner_email VARCHAR(191) NOT NULL,
                owner_role VARCHAR(50) NOT NULL,
                repository_url TEXT NOT NULL,
                namespace VARCHAR(64) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                deleted_at TIMESTAMPTZ NULL
            )
            """
        )
        cursor.execute(
            """
            ALTER TABLE workspace_ownership
            ADD COLUMN IF NOT EXISTS owner_user_id UUID NULL
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS workspace_ownership_org_owner_idx
            ON workspace_ownership (organization_id, owner_email)
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS workspace_ownership_org_owner_user_idx
            ON workspace_ownership (organization_id, owner_user_id)
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS workspace_access_audit (
                id BIGSERIAL PRIMARY KEY,
                workspace_id CHAR(32) NULL,
                organization_id VARCHAR(100) NOT NULL,
                actor_email VARCHAR(191) NOT NULL,
                actor_role VARCHAR(50) NOT NULL,
                event_type VARCHAR(100) NOT NULL,
                outcome VARCHAR(30) NOT NULL,
                request_id VARCHAR(64) NOT NULL,
                details JSONB NOT NULL DEFAULT '{}'::jsonb,
                occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        cursor.execute(
            """
            ALTER TABLE workspace_access_audit
            ADD COLUMN IF NOT EXISTS actor_user_id UUID NULL
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS workspace_access_audit_workspace_time_idx
            ON workspace_access_audit (workspace_id, occurred_at DESC)
            """
        )


def _row_to_ownership(row: dict[str, Any]) -> WorkspaceOwnership:
    owner_user_id = row.get("owner_user_id")
    return WorkspaceOwnership(
        workspace_id=str(row["workspace_id"]),
        organization_id=str(row["organization_id"]),
        owner_email=str(row["owner_email"]),
        owner_role=str(row["owner_role"]),
        repository_url=str(row["repository_url"]),
        namespace=str(row["namespace"]),
        created_at=row["created_at"],
        deleted_at=row.get("deleted_at"),
        owner_user_id=str(owner_user_id) if owner_user_id is not None else None,
    )


def register_workspace(*, workspace_id: str, principal, repository_url: str) -> WorkspaceOwnership:
    if not WORKSPACE_ID_RE.fullmatch(workspace_id):
        raise ValueError("Invalid workspace id")
    namespace = workspace_namespace(principal.organization_id, principal.email)
    owner_user_id = owner_user_id_from_principal(principal)
    with _connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            f"""
            INSERT INTO workspace_ownership (
                workspace_id, organization_id, owner_email, owner_role, owner_user_id,
                repository_url, namespace
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (workspace_id) DO NOTHING
            RETURNING {OWNERSHIP_SELECT_COLUMNS}
            """,
            (
                workspace_id,
                principal.organization_id,
                principal.email.casefold(),
                principal.role,
                owner_user_id,
                repository_url,
                namespace,
            ),
        )
        row = cursor.fetchone()
        if row is None:
            raise RuntimeError("Workspace ownership already exists")
    return _row_to_ownership(row)


def get_workspace_ownership(workspace_id: str) -> WorkspaceOwnership | None:
    if not WORKSPACE_ID_RE.fullmatch(workspace_id):
        return None
    with _connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT {OWNERSHIP_SELECT_COLUMNS}
            FROM workspace_ownership
            WHERE workspace_id = %s
            """,
            (workspace_id,),
        )
        row = cursor.fetchone()
    return _row_to_ownership(row) if row else None


def principal_can_access(principal, ownership: WorkspaceOwnership) -> bool:
    if ownership.deleted_at is not None:
        return False
    if principal.organization_id != ownership.organization_id:
        return False
    if principal.role in PRIVILEGED_ROLES:
        return True
    if ownership.owner_user_id is not None:
        return getattr(principal, "user_id", None) == ownership.owner_user_id
    return principal.email.casefold() == ownership.owner_email.casefold()


def list_accessible_active_workspaces(principal, limit: int = 20) -> list[WorkspaceOwnership]:
    """Return durable active workspaces the principal may recover, newest first.

    Discovery is ownership-registry only. Results stay organization-scoped and
    follow the same privilege rules as principal_can_access().
    """
    bounded_limit = max(1, min(int(limit), 20))
    organization_id = principal.organization_id
    with _connect() as connection, connection.cursor() as cursor:
        if principal.role in PRIVILEGED_ROLES:
            cursor.execute(
                f"""
                SELECT {OWNERSHIP_SELECT_COLUMNS}
                FROM workspace_ownership
                WHERE deleted_at IS NULL
                  AND organization_id = %s
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (organization_id, bounded_limit),
            )
        else:
            durable_uid = owner_user_id_from_principal(principal)
            if durable_uid is not None:
                cursor.execute(
                    f"""
                    SELECT {OWNERSHIP_SELECT_COLUMNS}
                    FROM workspace_ownership
                    WHERE deleted_at IS NULL
                      AND organization_id = %s
                      AND (
                        owner_user_id = %s
                        OR (owner_user_id IS NULL AND owner_email = %s)
                      )
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (organization_id, durable_uid, principal.email.casefold(), bounded_limit),
                )
            else:
                cursor.execute(
                    f"""
                    SELECT {OWNERSHIP_SELECT_COLUMNS}
                    FROM workspace_ownership
                    WHERE deleted_at IS NULL
                      AND organization_id = %s
                      AND owner_email = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (organization_id, principal.email.casefold(), bounded_limit),
                )
        rows = cursor.fetchall()
    return [_row_to_ownership(row) for row in rows]


def mark_workspace_deleted(workspace_id: str) -> None:
    with _connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            "UPDATE workspace_ownership SET deleted_at = NOW() WHERE workspace_id = %s AND deleted_at IS NULL",
            (workspace_id,),
        )


def record_workspace_audit(
    *,
    principal,
    event_type: str,
    outcome: str,
    request_id: str,
    workspace_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    safe_details = details or {}
    actor_user_id = actor_user_id_from_principal(principal)
    with _connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO workspace_access_audit (
                workspace_id, organization_id, actor_email, actor_role, actor_user_id,
                event_type, outcome, request_id, details
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            """,
            (
                workspace_id,
                principal.organization_id,
                principal.email.casefold(),
                principal.role,
                actor_user_id,
                event_type[:100],
                outcome[:30],
                request_id[:64],
                json.dumps(safe_details, separators=(",", ":"), sort_keys=True),
            ),
        )
