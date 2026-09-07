from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import re
from typing import Any

import psycopg
from psycopg.rows import dict_row

from app.core.config import get_settings

WORKSPACE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
PRIVILEGED_ROLES = frozenset({"platform_owner", "org_admin"})


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


def workspace_namespace(organization_id: str, owner_email: str) -> str:
    material = f"{organization_id.strip().casefold()}\0{owner_email.strip().casefold()}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()[:32]


def _connect():
    return psycopg.connect(get_settings().database_url, autocommit=True, row_factory=dict_row)


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
            CREATE INDEX IF NOT EXISTS workspace_ownership_org_owner_idx
            ON workspace_ownership (organization_id, owner_email)
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
            CREATE INDEX IF NOT EXISTS workspace_access_audit_workspace_time_idx
            ON workspace_access_audit (workspace_id, occurred_at DESC)
            """
        )


def register_workspace(*, workspace_id: str, principal, repository_url: str) -> WorkspaceOwnership:
    if not WORKSPACE_ID_RE.fullmatch(workspace_id):
        raise ValueError("Invalid workspace id")
    namespace = workspace_namespace(principal.organization_id, principal.email)
    with _connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO workspace_ownership (
                workspace_id, organization_id, owner_email, owner_role, repository_url, namespace
            ) VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (workspace_id) DO NOTHING
            RETURNING workspace_id, organization_id, owner_email, owner_role, repository_url, namespace, created_at, deleted_at
            """,
            (
                workspace_id,
                principal.organization_id,
                principal.email.casefold(),
                principal.role,
                repository_url,
                namespace,
            ),
        )
        row = cursor.fetchone()
        if row is None:
            raise RuntimeError("Workspace ownership already exists")
    return WorkspaceOwnership(**row)


def get_workspace_ownership(workspace_id: str) -> WorkspaceOwnership | None:
    if not WORKSPACE_ID_RE.fullmatch(workspace_id):
        return None
    with _connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT workspace_id, organization_id, owner_email, owner_role, repository_url, namespace, created_at, deleted_at
            FROM workspace_ownership
            WHERE workspace_id = %s
            """,
            (workspace_id,),
        )
        row = cursor.fetchone()
    return WorkspaceOwnership(**row) if row else None


def principal_can_access(principal, ownership: WorkspaceOwnership) -> bool:
    if ownership.deleted_at is not None:
        return False
    if principal.organization_id != ownership.organization_id:
        return False
    if principal.role in PRIVILEGED_ROLES:
        return True
    return principal.email.casefold() == ownership.owner_email.casefold()


def list_accessible_active_workspaces(principal, limit: int = 20) -> list[WorkspaceOwnership]:
    """Return durable active workspaces the principal may recover, newest first.

    Discovery is ownership-registry only. Results stay organization-scoped and
    follow the same privilege rules as principal_can_access().
    """
    bounded_limit = max(1, min(int(limit), 20))
    organization_id = principal.organization_id
    select_columns = (
        "workspace_id, organization_id, owner_email, owner_role, repository_url, "
        "namespace, created_at, deleted_at"
    )
    with _connect() as connection, connection.cursor() as cursor:
        if principal.role in PRIVILEGED_ROLES:
            cursor.execute(
                f"""
                SELECT {select_columns}
                FROM workspace_ownership
                WHERE deleted_at IS NULL
                  AND organization_id = %s
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (organization_id, bounded_limit),
            )
        else:
            cursor.execute(
                f"""
                SELECT {select_columns}
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
    return [WorkspaceOwnership(**row) for row in rows]


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
    with _connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO workspace_access_audit (
                workspace_id, organization_id, actor_email, actor_role, event_type, outcome, request_id, details
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            """,
            (
                workspace_id,
                principal.organization_id,
                principal.email.casefold(),
                principal.role,
                event_type[:100],
                outcome[:30],
                request_id[:64],
                json.dumps(safe_details, separators=(",", ":"), sort_keys=True),
            ),
        )
