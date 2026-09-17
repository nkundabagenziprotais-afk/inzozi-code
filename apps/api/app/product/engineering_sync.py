from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.core.config import get_settings
from app.product.store import (
    ProductNotFoundError,
    ProductStoreError,
    ProductValidationError,
    ensure_product_schema,
)

ENGINEERING_EVENT_TYPES = frozenset(
    {
        "engineering_opened",
        "workspace_opened",
        "command_completed",
        "git_review_prepared",
        "commit_created",
        "push_completed",
        "pull_request_created",
        "agent_run_completed",
    }
)
ENGINEERING_PROGRESS_EVENT_TYPES = frozenset(
    {
        "workspace_opened",
        "command_completed",
        "git_review_prepared",
        "commit_created",
        "push_completed",
        "pull_request_created",
        "agent_run_completed",
    }
)
ENGINEERING_EVENT_STATUSES = frozenset({"info", "success", "failure"})
ENGINEERING_SYNC_STATUSES = frozenset({"ready", "active", "attention"})


class EngineeringSyncError(ProductStoreError):
    """Product ↔ engineering synchronization failed."""


def _connect(*, autocommit: bool = True):
    try:
        return psycopg.connect(
            get_settings().database_url,
            autocommit=autocommit,
            row_factory=dict_row,
        )
    except Exception as exc:  # noqa: BLE001
        raise EngineeringSyncError("Engineering synchronization store unavailable") from exc


def ensure_engineering_sync_schema() -> None:
    """Create the additive synchronization schema after Product Control exists."""

    ensure_product_schema()
    with _connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS aquila_product_engineering_bindings (
                binding_id UUID PRIMARY KEY,
                product_id UUID NOT NULL UNIQUE
                    REFERENCES aquila_products(product_id) ON DELETE CASCADE,
                module_id UUID NULL
                    REFERENCES aquila_product_modules(module_id) ON DELETE SET NULL,
                deliverable_id UUID NULL
                    REFERENCES aquila_product_deliverables(deliverable_id) ON DELETE SET NULL,
                repository_url TEXT NULL,
                workspace_id VARCHAR(160) NULL,
                repository_ref VARCHAR(255) NULL,
                sync_status VARCHAR(20) NOT NULL DEFAULT 'ready',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT aquila_engineering_binding_sync_status_check
                    CHECK (sync_status IN ('ready', 'active', 'attention'))
            )
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS aquila_engineering_bindings_workspace_idx
            ON aquila_product_engineering_bindings (workspace_id)
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS aquila_product_engineering_events (
                event_id UUID PRIMARY KEY,
                product_id UUID NOT NULL
                    REFERENCES aquila_products(product_id) ON DELETE CASCADE,
                binding_id UUID NULL
                    REFERENCES aquila_product_engineering_bindings(binding_id) ON DELETE SET NULL,
                module_id UUID NULL
                    REFERENCES aquila_product_modules(module_id) ON DELETE SET NULL,
                deliverable_id UUID NULL
                    REFERENCES aquila_product_deliverables(deliverable_id) ON DELETE SET NULL,
                event_type VARCHAR(40) NOT NULL,
                status VARCHAR(20) NOT NULL,
                summary VARCHAR(500) NOT NULL,
                evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT aquila_engineering_event_status_check
                    CHECK (status IN ('info', 'success', 'failure'))
            )
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS aquila_engineering_events_product_created_idx
            ON aquila_product_engineering_events (product_id, created_at DESC)
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS aquila_product_module_deliverables (
                assignment_id UUID PRIMARY KEY,
                product_id UUID NOT NULL
                    REFERENCES aquila_products(product_id) ON DELETE CASCADE,
                module_id UUID NOT NULL
                    REFERENCES aquila_product_modules(module_id) ON DELETE CASCADE,
                deliverable_id UUID NOT NULL
                    REFERENCES aquila_product_deliverables(deliverable_id) ON DELETE CASCADE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT aquila_module_deliverable_unique
                    UNIQUE (product_id, deliverable_id),
                CONSTRAINT aquila_module_deliverable_pair_unique
                    UNIQUE (product_id, module_id, deliverable_id)
            )
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS aquila_module_deliverables_module_idx
            ON aquila_product_module_deliverables (product_id, module_id, created_at)
            """
        )


def _serialize(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None:
        return None
    return str(value) if value.__class__.__name__ == "UUID" else value


def _serialize_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: _serialize(value) for key, value in row.items()}


def _assert_product_access(cursor, *, product_id: str, organization_id: str) -> None:
    cursor.execute(
        "SELECT 1 FROM aquila_products WHERE product_id = %s AND organization_id = %s",
        (product_id, organization_id),
    )
    if not cursor.fetchone():
        raise ProductNotFoundError("Product not found")


def _assert_child_belongs_to_product(
    cursor,
    *,
    product_id: str,
    child_id: str | None,
    table: str,
    id_column: str,
    label: str,
) -> None:
    if child_id is None:
        return
    cursor.execute(
        f"SELECT 1 FROM {table} WHERE {id_column} = %s AND product_id = %s",
        (child_id, product_id),
    )
    if not cursor.fetchone():
        raise ProductValidationError(f"{label} does not belong to this product")


def _module_proposals(cursor, *, product_id: str) -> list[dict[str, str]]:
    """Return review-only starting modules; nothing is persisted until explicitly added."""

    cursor.execute(
        """
        SELECT p.name, b.concept, b.capabilities
        FROM aquila_products p
        LEFT JOIN aquila_product_blueprints b ON b.product_id = p.product_id
        WHERE p.product_id = %s
        """,
        (product_id,),
    )
    product = cursor.fetchone() or {}
    text = f"{product.get('name') or ''} {product.get('concept') or ''}".casefold()
    capabilities = set(product.get("capabilities") or [])

    if any(token in text for token in ("school", "student", "education", "academic")):
        proposals = [
            ("Admissions & Enrollment", "Manage applications, admissions, enrollment and student intake.", "must_have"),
            ("Student Management", "Maintain student profiles, guardians, classes and lifecycle records.", "must_have"),
            ("Academic Management", "Manage subjects, classes, assessments, grades and academic progression.", "must_have"),
            ("Fees & Finance", "Manage fee structures, billing, collections, balances and finance controls.", "must_have"),
            ("Attendance", "Track student and staff attendance with follow-up workflows.", "should_have"),
            ("Parent / Student Portal", "Provide self-service access to results, balances, notices and requests.", "should_have"),
            ("Reporting & Analytics", "Provide operational and management reporting across the school.", "good_to_have"),
        ]
    elif any(token in text for token in ("farm", "agri", "crop", "livestock", "farmer")):
        proposals = [
            ("Farm & Plot Registry", "Maintain farms, plots, locations, ownership and production units.", "must_have"),
            ("Farmer / Owner Management", "Maintain farmer, owner, worker and stakeholder profiles.", "must_have"),
            ("Production Management", "Plan and track crop, livestock or production cycles and outputs.", "must_have"),
            ("Inputs & Inventory", "Manage inputs, stock, equipment and consumption records.", "must_have"),
            ("Field Activities", "Plan, assign and track field work and operational tasks.", "should_have"),
            ("Harvest, Sales & Market", "Track harvests, sales, customers, pricing and market transactions.", "should_have"),
            ("Reporting & Analytics", "Provide farm performance, production and management insights.", "good_to_have"),
        ]
    else:
        proposals = [
            ("Core Records & Profiles", "Maintain the core business records and profiles used by the solution.", "must_have"),
            ("Workflow & Operations", "Manage the main operational workflow from initiation through completion.", "must_have"),
            ("Administration & Access", "Manage configuration, roles, permissions and administrative controls.", "must_have"),
            ("Reporting & Analytics", "Provide operational and management reports and dashboards.", "should_have"),
            ("Notifications & Communication", "Support alerts, reminders and user communication.", "should_have"),
        ]
        if "external_integrations" in capabilities:
            proposals.append(
                ("External Integrations", "Coordinate partner and third-party service integrations.", "good_to_have")
            )

    cursor.execute(
        "SELECT LOWER(name) AS name FROM aquila_product_modules WHERE product_id = %s",
        (product_id,),
    )
    existing = {row["name"] for row in cursor.fetchall()}
    return [
        {
            "name": name,
            "description": description,
            "priority": priority,
            "proposal_state": "review_only",
        }
        for name, description, priority in proposals
        if name.casefold() not in existing
    ]


def _read_module_delivery(cursor, *, product_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    cursor.execute(
        """
        SELECT m.module_id, m.sequence, m.name, m.priority, m.status,
               COUNT(md.deliverable_id)::int AS deliverable_total,
               COUNT(md.deliverable_id) FILTER (WHERE d.status = 'complete')::int AS deliverable_complete,
               COUNT(md.deliverable_id) FILTER (WHERE d.status = 'blocked')::int AS deliverable_blocked,
               COUNT(md.deliverable_id) FILTER (WHERE d.status = 'in_progress')::int AS deliverable_in_progress
        FROM aquila_product_modules m
        LEFT JOIN aquila_product_module_deliverables md
          ON md.product_id = m.product_id AND md.module_id = m.module_id
        LEFT JOIN aquila_product_deliverables d
          ON d.product_id = md.product_id AND d.deliverable_id = md.deliverable_id
        WHERE m.product_id = %s
        GROUP BY m.module_id, m.sequence, m.name, m.priority, m.status
        ORDER BY m.sequence, m.created_at
        """,
        (product_id,),
    )
    modules = []
    for row in cursor.fetchall():
        item = _serialize_row(row)
        total = int(item["deliverable_total"])
        complete = int(item["deliverable_complete"])
        blocked = int(item["deliverable_blocked"])
        in_progress = int(item["deliverable_in_progress"])
        item["progress_percent"] = round((complete / total) * 100) if total else (
            100 if item["status"] == "complete" else 0
        )
        if total:
            item["derived_status"] = (
                "complete" if complete == total
                else "blocked" if blocked
                else "in_progress" if in_progress or complete
                else "planned"
            )
        else:
            item["derived_status"] = item["status"]
        modules.append(item)

    cursor.execute(
        """
        SELECT d.deliverable_id, d.sequence, d.title, d.description, d.status,
               md.module_id, m.name AS module_name
        FROM aquila_product_deliverables d
        LEFT JOIN aquila_product_module_deliverables md
          ON md.product_id = d.product_id AND md.deliverable_id = d.deliverable_id
        LEFT JOIN aquila_product_modules m
          ON m.product_id = md.product_id AND m.module_id = md.module_id
        WHERE d.product_id = %s
        ORDER BY d.sequence
        """,
        (product_id,),
    )
    deliverables = [_serialize_row(row) for row in cursor.fetchall()]
    return modules, deliverables


def _read_summary(cursor, *, product_id: str) -> dict[str, Any]:
    cursor.execute(
        """
        SELECT b.binding_id, b.product_id,
               COALESCE(md.module_id, b.module_id) AS module_id,
               COALESCE(assigned_module.name, bound_module.name) AS module_name,
               b.deliverable_id, d.title AS deliverable_title,
               b.repository_url, b.workspace_id, b.repository_ref,
               b.sync_status, b.created_at, b.updated_at
        FROM aquila_product_engineering_bindings b
        LEFT JOIN aquila_product_module_deliverables md
          ON md.product_id = b.product_id
         AND md.deliverable_id = b.deliverable_id
        LEFT JOIN aquila_product_modules assigned_module
          ON assigned_module.product_id = b.product_id
         AND assigned_module.module_id = md.module_id
        LEFT JOIN aquila_product_modules bound_module
          ON bound_module.product_id = b.product_id
         AND bound_module.module_id = b.module_id
        LEFT JOIN aquila_product_deliverables d
          ON d.product_id = b.product_id
         AND d.deliverable_id = b.deliverable_id
        WHERE b.product_id = %s
        """,
        (product_id,),
    )
    binding = cursor.fetchone()

    cursor.execute(
        """
        SELECT e.event_id, e.event_type, e.status, e.summary, e.evidence,
               e.module_id, m.name AS module_name,
               e.deliverable_id, d.title AS deliverable_title,
               e.created_at
        FROM aquila_product_engineering_events e
        LEFT JOIN aquila_product_modules m ON m.module_id = e.module_id
        LEFT JOIN aquila_product_deliverables d ON d.deliverable_id = e.deliverable_id
        WHERE e.product_id = %s
        ORDER BY e.created_at DESC, e.event_id DESC
        LIMIT 20
        """,
        (product_id,),
    )
    events = cursor.fetchall()

    cursor.execute(
        "SELECT COUNT(*) AS total FROM aquila_product_engineering_events WHERE product_id = %s",
        (product_id,),
    )
    evidence_count = int(cursor.fetchone()["total"])

    module_delivery, delivery_deliverables = _read_module_delivery(cursor, product_id=product_id)
    linked_total = sum(int(item["deliverable_total"]) for item in module_delivery)
    linked_complete = sum(int(item["deliverable_complete"]) for item in module_delivery)
    linked_blocked = sum(int(item["deliverable_blocked"]) for item in module_delivery)
    modules_complete = sum(1 for item in module_delivery if item["derived_status"] == "complete")

    serialized_binding = _serialize_row(binding) if binding else None
    serialized_events = [_serialize_row(row) for row in events]
    last_activity_at = serialized_events[0]["created_at"] if serialized_events else (
        serialized_binding["updated_at"] if serialized_binding else None
    )

    return {
        "binding": serialized_binding,
        "recent_events": serialized_events,
        "evidence_count": evidence_count,
        "last_activity_at": last_activity_at,
        "sync_health": serialized_binding["sync_status"] if serialized_binding else "not_linked",
        "module_delivery": module_delivery,
        "delivery_deliverables": delivery_deliverables,
        "module_delivery_summary": {
            "modules_total": len(module_delivery),
            "modules_complete": modules_complete,
            "linked_deliverables_total": linked_total,
            "linked_deliverables_complete": linked_complete,
            "linked_deliverables_blocked": linked_blocked,
            "progress_percent": round((linked_complete / linked_total) * 100) if linked_total else 0,
        },
        "module_proposals": _module_proposals(cursor, product_id=product_id),
    }


def get_engineering_summary(*, product_id: str, organization_id: str) -> dict[str, Any]:
    ensure_engineering_sync_schema()
    try:
        with _connect() as connection, connection.cursor() as cursor:
            _assert_product_access(cursor, product_id=product_id, organization_id=organization_id)
            return _read_summary(cursor, product_id=product_id)
    except (ProductNotFoundError, ProductValidationError):
        raise
    except ProductStoreError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise EngineeringSyncError("Unable to read engineering synchronization") from exc


def assign_deliverable_to_module(
    *,
    product_id: str,
    organization_id: str,
    deliverable_id: str,
    module_id: str | None,
) -> dict[str, Any]:
    """Attach one deliverable to one intended module, or clear the assignment."""

    ensure_engineering_sync_schema()
    try:
        with _connect(autocommit=False) as connection, connection.cursor() as cursor:
            _assert_product_access(cursor, product_id=product_id, organization_id=organization_id)
            _assert_child_belongs_to_product(
                cursor,
                product_id=product_id,
                child_id=deliverable_id,
                table="aquila_product_deliverables",
                id_column="deliverable_id",
                label="Deliverable",
            )
            _assert_child_belongs_to_product(
                cursor,
                product_id=product_id,
                child_id=module_id,
                table="aquila_product_modules",
                id_column="module_id",
                label="Module",
            )

            if module_id is None:
                cursor.execute(
                    """
                    DELETE FROM aquila_product_module_deliverables
                    WHERE product_id = %s AND deliverable_id = %s
                    """,
                    (product_id, deliverable_id),
                )
                cursor.execute(
                    """
                    UPDATE aquila_product_engineering_bindings
                    SET deliverable_id = NULL, updated_at = NOW()
                    WHERE product_id = %s AND deliverable_id = %s
                    """,
                    (product_id, deliverable_id),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO aquila_product_module_deliverables (
                        assignment_id, product_id, module_id, deliverable_id
                    ) VALUES (%s, %s, %s, %s)
                    ON CONFLICT (product_id, deliverable_id)
                    DO UPDATE SET module_id = EXCLUDED.module_id
                    """,
                    (str(uuid4()), product_id, module_id, deliverable_id),
                )
                cursor.execute(
                    """
                    UPDATE aquila_product_engineering_bindings
                    SET module_id = %s, updated_at = NOW()
                    WHERE product_id = %s AND deliverable_id = %s
                    """,
                    (module_id, product_id, deliverable_id),
                )

            cursor.execute(
                "UPDATE aquila_products SET updated_at = NOW() WHERE product_id = %s",
                (product_id,),
            )
            connection.commit()
            return _read_summary(cursor, product_id=product_id)
    except (ProductNotFoundError, ProductValidationError):
        raise
    except ProductStoreError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise EngineeringSyncError("Unable to assign deliverable to module") from exc


def _resolve_module_for_deliverable(
    cursor,
    *,
    product_id: str,
    module_id: str | None,
    deliverable_id: str | None,
) -> str | None:
    if deliverable_id is None:
        return module_id

    cursor.execute(
        """
        SELECT module_id
        FROM aquila_product_module_deliverables
        WHERE product_id = %s AND deliverable_id = %s
        """,
        (product_id, deliverable_id),
    )
    assignment = cursor.fetchone()
    if not assignment:
        raise ProductValidationError(
            "Assign this deliverable to an intended module before continuing in Engineering Space"
        )
    assigned_module_id = str(assignment["module_id"])
    if module_id is not None and str(module_id) != assigned_module_id:
        raise ProductValidationError("Deliverable is assigned to a different intended module")
    return assigned_module_id


def update_engineering_binding(
    *,
    product_id: str,
    organization_id: str,
    changes: dict[str, Any],
) -> dict[str, Any]:
    ensure_engineering_sync_schema()
    allowed = {
        "module_id",
        "deliverable_id",
        "repository_url",
        "workspace_id",
        "repository_ref",
    }
    unknown = set(changes) - allowed
    if unknown:
        raise ProductValidationError("Unsupported engineering binding fields")

    try:
        with _connect(autocommit=False) as connection, connection.cursor() as cursor:
            _assert_product_access(cursor, product_id=product_id, organization_id=organization_id)
            cursor.execute(
                "SELECT * FROM aquila_product_engineering_bindings WHERE product_id = %s",
                (product_id,),
            )
            existing = cursor.fetchone()

            module_id = changes.get("module_id", existing["module_id"] if existing else None)
            deliverable_id = changes.get(
                "deliverable_id",
                existing["deliverable_id"] if existing else None,
            )
            repository_url = changes.get(
                "repository_url",
                existing["repository_url"] if existing else None,
            )
            workspace_id = changes.get(
                "workspace_id",
                existing["workspace_id"] if existing else None,
            )
            repository_ref = changes.get(
                "repository_ref",
                existing["repository_ref"] if existing else None,
            )

            _assert_child_belongs_to_product(
                cursor,
                product_id=product_id,
                child_id=module_id,
                table="aquila_product_modules",
                id_column="module_id",
                label="Module",
            )
            _assert_child_belongs_to_product(
                cursor,
                product_id=product_id,
                child_id=deliverable_id,
                table="aquila_product_deliverables",
                id_column="deliverable_id",
                label="Deliverable",
            )
            module_candidate = (
                str(module_id) if module_id is not None else None
            ) if ("module_id" in changes or deliverable_id is None) else None
            module_id = _resolve_module_for_deliverable(
                cursor,
                product_id=product_id,
                module_id=module_candidate,
                deliverable_id=str(deliverable_id) if deliverable_id is not None else None,
            )

            if existing:
                cursor.execute(
                    """
                    UPDATE aquila_product_engineering_bindings
                    SET module_id = %s,
                        deliverable_id = %s,
                        repository_url = %s,
                        workspace_id = %s,
                        repository_ref = %s,
                        updated_at = NOW()
                    WHERE product_id = %s
                    """,
                    (
                        module_id,
                        deliverable_id,
                        repository_url,
                        workspace_id,
                        repository_ref,
                        product_id,
                    ),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO aquila_product_engineering_bindings (
                        binding_id, product_id, module_id, deliverable_id,
                        repository_url, workspace_id, repository_ref, sync_status
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'ready')
                    """,
                    (
                        str(uuid4()),
                        product_id,
                        module_id,
                        deliverable_id,
                        repository_url,
                        workspace_id,
                        repository_ref,
                    ),
                )

            cursor.execute(
                "UPDATE aquila_products SET updated_at = NOW() WHERE product_id = %s",
                (product_id,),
            )
            connection.commit()
            return _read_summary(cursor, product_id=product_id)
    except (ProductNotFoundError, ProductValidationError):
        raise
    except ProductStoreError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise EngineeringSyncError("Unable to update engineering binding") from exc


def record_engineering_event(
    *,
    product_id: str,
    organization_id: str,
    event_type: str,
    status: str,
    summary: str,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ensure_engineering_sync_schema()
    if event_type not in ENGINEERING_EVENT_TYPES:
        raise ProductValidationError("Unsupported engineering event type")
    if status not in ENGINEERING_EVENT_STATUSES:
        raise ProductValidationError("Unsupported engineering event status")
    clean_summary = summary.strip()
    if not clean_summary:
        raise ProductValidationError("Engineering event summary is required")

    try:
        with _connect(autocommit=False) as connection, connection.cursor() as cursor:
            _assert_product_access(cursor, product_id=product_id, organization_id=organization_id)
            cursor.execute(
                "SELECT * FROM aquila_product_engineering_bindings WHERE product_id = %s",
                (product_id,),
            )
            binding = cursor.fetchone()
            if not binding:
                binding_id = str(uuid4())
                cursor.execute(
                    """
                    INSERT INTO aquila_product_engineering_bindings (
                        binding_id, product_id, sync_status
                    ) VALUES (%s, %s, 'ready')
                    """,
                    (binding_id, product_id),
                )
                cursor.execute(
                    "SELECT * FROM aquila_product_engineering_bindings WHERE product_id = %s",
                    (product_id,),
                )
                binding = cursor.fetchone()

            module_id = binding["module_id"]
            deliverable_id = binding["deliverable_id"]
            cursor.execute(
                """
                INSERT INTO aquila_product_engineering_events (
                    event_id, product_id, binding_id, module_id, deliverable_id,
                    event_type, status, summary, evidence
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    str(uuid4()),
                    product_id,
                    binding["binding_id"],
                    module_id,
                    deliverable_id,
                    event_type,
                    status,
                    clean_summary[:500],
                    Jsonb(evidence or {}),
                ),
            )

            qualifies_for_progress = (
                status != "failure"
                and event_type in ENGINEERING_PROGRESS_EVENT_TYPES
            )
            if qualifies_for_progress:
                if module_id:
                    cursor.execute(
                        """
                        UPDATE aquila_product_modules
                        SET status = 'in_progress', updated_at = NOW()
                        WHERE module_id = %s AND product_id = %s AND status = 'planned'
                        """,
                        (module_id, product_id),
                    )
                if deliverable_id:
                    cursor.execute(
                        """
                        UPDATE aquila_product_deliverables
                        SET status = 'in_progress', updated_at = NOW()
                        WHERE deliverable_id = %s AND product_id = %s AND status = 'planned'
                        """,
                        (deliverable_id, product_id),
                    )

            sync_status = (
                "attention"
                if status == "failure"
                else "active"
                if qualifies_for_progress
                else binding["sync_status"]
            )
            cursor.execute(
                """
                UPDATE aquila_product_engineering_bindings
                SET sync_status = %s, updated_at = NOW()
                WHERE binding_id = %s
                """,
                (sync_status, binding["binding_id"]),
            )
            cursor.execute(
                "UPDATE aquila_products SET updated_at = NOW() WHERE product_id = %s",
                (product_id,),
            )
            connection.commit()
            return _read_summary(cursor, product_id=product_id)
    except (ProductNotFoundError, ProductValidationError):
        raise
    except ProductStoreError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise EngineeringSyncError("Unable to record engineering evidence") from exc
