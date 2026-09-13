from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.core.config import get_settings

PRODUCT_STATUSES = frozenset({"planning", "active", "paused", "completed", "archived"})
WORK_STATUSES = frozenset({"planned", "in_progress", "blocked", "complete"})
MODULE_PRIORITIES = frozenset({"must_have", "should_have", "good_to_have"})


class ProductStoreError(RuntimeError):
    """Product-control persistence failed or is unavailable."""


class ProductNotFoundError(ProductStoreError):
    """Requested product or child record is not available to this organization."""


class ProductValidationError(ProductStoreError):
    """Requested product-control mutation is invalid."""


def _connect(*, autocommit: bool = True):
    try:
        return psycopg.connect(get_settings().database_url, autocommit=autocommit, row_factory=dict_row)
    except Exception as exc:  # noqa: BLE001 - product state must fail closed
        raise ProductStoreError("Product store unavailable") from exc


def ensure_product_schema() -> None:
    with _connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS aquila_products (
                product_id UUID PRIMARY KEY,
                organization_id VARCHAR(100) NOT NULL,
                name VARCHAR(160) NOT NULL,
                summary TEXT NOT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'planning',
                owner_user_id UUID NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT aquila_products_status_check
                    CHECK (status IN ('planning', 'active', 'paused', 'completed', 'archived'))
            )
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS aquila_products_org_updated_idx
            ON aquila_products (organization_id, updated_at DESC)
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS aquila_product_blueprints (
                product_id UUID PRIMARY KEY REFERENCES aquila_products(product_id) ON DELETE CASCADE,
                concept TEXT NOT NULL,
                target_users JSONB NOT NULL DEFAULT '[]'::jsonb,
                platforms JSONB NOT NULL DEFAULT '[]'::jsonb,
                capabilities JSONB NOT NULL DEFAULT '[]'::jsonb,
                constraints JSONB NOT NULL DEFAULT '[]'::jsonb,
                blueprint_version INTEGER NOT NULL DEFAULT 1,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT aquila_blueprint_version_positive CHECK (blueprint_version > 0)
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS aquila_product_components (
                component_id UUID PRIMARY KEY,
                product_id UUID NOT NULL REFERENCES aquila_products(product_id) ON DELETE CASCADE,
                component_key VARCHAR(120) NOT NULL,
                kind VARCHAR(40) NOT NULL,
                name VARCHAR(160) NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                platform VARCHAR(40) NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'planned',
                repository_url TEXT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT aquila_component_status_check
                    CHECK (status IN ('planned', 'in_progress', 'blocked', 'complete')),
                CONSTRAINT aquila_component_key_unique UNIQUE (product_id, component_key)
            )
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS aquila_components_product_kind_idx
            ON aquila_product_components (product_id, kind, created_at)
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS aquila_product_modules (
                module_id UUID PRIMARY KEY,
                product_id UUID NOT NULL REFERENCES aquila_products(product_id) ON DELETE CASCADE,
                sequence INTEGER NOT NULL,
                name VARCHAR(160) NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                priority VARCHAR(20) NOT NULL DEFAULT 'must_have',
                status VARCHAR(20) NOT NULL DEFAULT 'planned',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT aquila_module_priority_check
                    CHECK (priority IN ('must_have', 'should_have', 'good_to_have')),
                CONSTRAINT aquila_module_status_check
                    CHECK (status IN ('planned', 'in_progress', 'blocked', 'complete')),
                CONSTRAINT aquila_module_sequence_positive CHECK (sequence > 0),
                CONSTRAINT aquila_module_sequence_unique UNIQUE (product_id, sequence),
                CONSTRAINT aquila_module_name_unique UNIQUE (product_id, name)
            )
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS aquila_modules_product_priority_sequence_idx
            ON aquila_product_modules (product_id, priority, sequence)
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS aquila_product_deliverables (
                deliverable_id UUID PRIMARY KEY,
                product_id UUID NOT NULL REFERENCES aquila_products(product_id) ON DELETE CASCADE,
                component_id UUID NULL REFERENCES aquila_product_components(component_id) ON DELETE SET NULL,
                sequence INTEGER NOT NULL,
                title VARCHAR(200) NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                status VARCHAR(20) NOT NULL DEFAULT 'planned',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT aquila_deliverable_status_check
                    CHECK (status IN ('planned', 'in_progress', 'blocked', 'complete')),
                CONSTRAINT aquila_deliverable_sequence_positive CHECK (sequence > 0),
                CONSTRAINT aquila_deliverable_sequence_unique UNIQUE (product_id, sequence)
            )
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS aquila_deliverables_product_sequence_idx
            ON aquila_product_deliverables (product_id, sequence)
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS aquila_product_dependencies (
                dependency_id UUID PRIMARY KEY,
                product_id UUID NOT NULL REFERENCES aquila_products(product_id) ON DELETE CASCADE,
                upstream_component_id UUID NOT NULL REFERENCES aquila_product_components(component_id) ON DELETE CASCADE,
                downstream_component_id UUID NOT NULL REFERENCES aquila_product_components(component_id) ON DELETE CASCADE,
                relationship VARCHAR(60) NOT NULL,
                impact_note TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT aquila_dependency_not_self CHECK (upstream_component_id <> downstream_component_id),
                CONSTRAINT aquila_dependency_unique
                    UNIQUE (product_id, upstream_component_id, downstream_component_id, relationship)
            )
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


def _progress(deliverables: list[dict[str, Any]]) -> int:
    if not deliverables:
        return 0
    complete = sum(1 for item in deliverables if item.get("status") == "complete")
    return round((complete / len(deliverables)) * 100)


def _module_progress(modules: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "total": len(modules),
        "complete": 0,
        "pending": 0,
        "blocked": 0,
        "progress_percent": 0,
        "priorities": {
            "must_have": {"total": 0, "complete": 0},
            "should_have": {"total": 0, "complete": 0},
            "good_to_have": {"total": 0, "complete": 0},
        },
    }
    for module in modules:
        priority = module.get("priority")
        status = module.get("status")
        if priority in summary["priorities"]:
            summary["priorities"][priority]["total"] += 1
        if status == "complete":
            summary["complete"] += 1
            if priority in summary["priorities"]:
                summary["priorities"][priority]["complete"] += 1
        else:
            summary["pending"] += 1
        if status == "blocked":
            summary["blocked"] += 1
    if summary["total"]:
        summary["progress_percent"] = round((summary["complete"] / summary["total"]) * 100)
    return summary


def create_product(
    *,
    organization_id: str,
    owner_user_id: str,
    name: str,
    summary: str,
    concept: str,
    target_users: list[str],
    platforms: list[str],
    capabilities: list[str],
    constraints: list[str],
    components: list[dict[str, Any]],
    deliverables: list[dict[str, Any]],
    dependencies: list[dict[str, Any]],
    modules: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    product_id = str(uuid4())
    component_ids = {item["component_key"]: str(uuid4()) for item in components}

    try:
        with _connect(autocommit=False) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO aquila_products (
                    product_id, organization_id, name, summary, status, owner_user_id
                ) VALUES (%s, %s, %s, %s, 'planning', %s)
                """,
                (product_id, organization_id, name, summary, owner_user_id),
            )
            cursor.execute(
                """
                INSERT INTO aquila_product_blueprints (
                    product_id, concept, target_users, platforms, capabilities, constraints
                ) VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    product_id,
                    concept,
                    Jsonb(target_users),
                    Jsonb(platforms),
                    Jsonb(capabilities),
                    Jsonb(constraints),
                ),
            )
            for component in components:
                cursor.execute(
                    """
                    INSERT INTO aquila_product_components (
                        component_id, product_id, component_key, kind, name,
                        description, platform, status
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        component_ids[component["component_key"]],
                        product_id,
                        component["component_key"],
                        component["kind"],
                        component["name"],
                        component.get("description", ""),
                        component.get("platform"),
                        component.get("status", "planned"),
                    ),
                )
            for index, module in enumerate(modules or [], start=1):
                priority = module.get("priority", "must_have")
                status = module.get("status", "planned")
                if priority not in MODULE_PRIORITIES or status not in WORK_STATUSES:
                    raise ProductValidationError("Invalid solution module priority or status")
                cursor.execute(
                    """
                    INSERT INTO aquila_product_modules (
                        module_id, product_id, sequence, name, description, priority, status
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        str(uuid4()),
                        product_id,
                        module.get("sequence", index),
                        module["name"].strip(),
                        module.get("description", "").strip(),
                        priority,
                        status,
                    ),
                )
            for deliverable in deliverables:
                component_key = deliverable.get("component_key")
                cursor.execute(
                    """
                    INSERT INTO aquila_product_deliverables (
                        deliverable_id, product_id, component_id, sequence,
                        title, description, status
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        str(uuid4()),
                        product_id,
                        component_ids.get(component_key) if component_key else None,
                        deliverable["sequence"],
                        deliverable["title"],
                        deliverable.get("description", ""),
                        deliverable.get("status", "planned"),
                    ),
                )
            for dependency in dependencies:
                upstream = component_ids.get(dependency["upstream_key"])
                downstream = component_ids.get(dependency["downstream_key"])
                if not upstream or not downstream or upstream == downstream:
                    continue
                cursor.execute(
                    """
                    INSERT INTO aquila_product_dependencies (
                        dependency_id, product_id, upstream_component_id,
                        downstream_component_id, relationship, impact_note
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    (
                        str(uuid4()),
                        product_id,
                        upstream,
                        downstream,
                        dependency.get("relationship", "depends_on"),
                        dependency.get("impact_note", ""),
                    ),
                )
            connection.commit()
    except ProductValidationError:
        raise
    except ProductStoreError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ProductStoreError("Unable to create product") from exc

    return get_product_snapshot(product_id=product_id, organization_id=organization_id)


def list_products(*, organization_id: str) -> list[dict[str, Any]]:
    try:
        with _connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT product_id, organization_id, name, summary, status,
                       owner_user_id, created_at, updated_at
                FROM aquila_products
                WHERE organization_id = %s
                ORDER BY updated_at DESC, created_at DESC
                """,
                (organization_id,),
            )
            products = cursor.fetchall()
            cursor.execute(
                """
                SELECT d.product_id,
                       COUNT(*) AS total,
                       COUNT(*) FILTER (WHERE d.status = 'complete') AS complete
                FROM aquila_product_deliverables d
                JOIN aquila_products p ON p.product_id = d.product_id
                WHERE p.organization_id = %s
                GROUP BY d.product_id
                """,
                (organization_id,),
            )
            progress_rows = cursor.fetchall()
    except ProductStoreError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ProductStoreError("Unable to list products") from exc

    progress_map = {
        str(row["product_id"]): round((int(row["complete"]) / int(row["total"])) * 100)
        if int(row["total"]) else 0
        for row in progress_rows
    }
    result = []
    for row in products:
        item = _serialize_row(row)
        item["progress_percent"] = progress_map.get(item["product_id"], 0)
        result.append(item)
    return result


def get_product_snapshot(*, product_id: str, organization_id: str) -> dict[str, Any]:
    try:
        with _connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT product_id, organization_id, name, summary, status,
                       owner_user_id, created_at, updated_at
                FROM aquila_products
                WHERE product_id = %s AND organization_id = %s
                """,
                (product_id, organization_id),
            )
            product = cursor.fetchone()
            if not product:
                raise ProductNotFoundError("Product not found")

            cursor.execute(
                """
                SELECT concept, target_users, platforms, capabilities, constraints,
                       blueprint_version, updated_at
                FROM aquila_product_blueprints
                WHERE product_id = %s
                """,
                (product_id,),
            )
            blueprint = cursor.fetchone() or {}

            cursor.execute(
                """
                SELECT component_id, component_key, kind, name, description,
                       platform, status, repository_url, created_at, updated_at
                FROM aquila_product_components
                WHERE product_id = %s
                ORDER BY created_at, name
                """,
                (product_id,),
            )
            components = cursor.fetchall()

            cursor.execute(
                """
                SELECT module_id, sequence, name, description, priority, status,
                       created_at, updated_at
                FROM aquila_product_modules
                WHERE product_id = %s
                ORDER BY sequence, created_at
                """,
                (product_id,),
            )
            modules = cursor.fetchall()

            cursor.execute(
                """
                SELECT deliverable_id, component_id, sequence, title, description,
                       status, created_at, updated_at
                FROM aquila_product_deliverables
                WHERE product_id = %s
                ORDER BY sequence
                """,
                (product_id,),
            )
            deliverables = cursor.fetchall()

            cursor.execute(
                """
                SELECT d.dependency_id,
                       d.upstream_component_id,
                       up.name AS upstream_name,
                       d.downstream_component_id,
                       down.name AS downstream_name,
                       d.relationship,
                       d.impact_note
                FROM aquila_product_dependencies d
                JOIN aquila_product_components up ON up.component_id = d.upstream_component_id
                JOIN aquila_product_components down ON down.component_id = d.downstream_component_id
                WHERE d.product_id = %s
                ORDER BY up.name, down.name
                """,
                (product_id,),
            )
            dependencies = cursor.fetchall()
    except ProductNotFoundError:
        raise
    except ProductStoreError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ProductStoreError("Unable to read product") from exc

    serialized_modules = [_serialize_row(row) for row in modules]
    serialized_deliverables = [_serialize_row(row) for row in deliverables]
    payload = _serialize_row(product)
    payload["blueprint"] = _serialize_row(blueprint)
    payload["components"] = [_serialize_row(row) for row in components]
    payload["modules"] = serialized_modules
    payload["module_progress"] = _module_progress(serialized_modules)
    payload["deliverables"] = serialized_deliverables
    payload["dependencies"] = [_serialize_row(row) for row in dependencies]
    payload["progress_percent"] = _progress(serialized_deliverables)
    return payload


def _assert_product_access(cursor, *, product_id: str, organization_id: str) -> None:
    cursor.execute(
        "SELECT 1 FROM aquila_products WHERE product_id = %s AND organization_id = %s",
        (product_id, organization_id),
    )
    if not cursor.fetchone():
        raise ProductNotFoundError("Product not found")


def create_module(
    *,
    product_id: str,
    organization_id: str,
    name: str,
    description: str,
    priority: str,
) -> dict[str, Any]:
    name = name.strip()
    description = description.strip()
    if not name:
        raise ProductValidationError("Module name is required")
    if priority not in MODULE_PRIORITIES:
        raise ProductValidationError("Invalid module priority")
    try:
        with _connect(autocommit=False) as connection, connection.cursor() as cursor:
            _assert_product_access(cursor, product_id=product_id, organization_id=organization_id)
            cursor.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence FROM aquila_product_modules WHERE product_id = %s",
                (product_id,),
            )
            sequence = int(cursor.fetchone()["next_sequence"])
            try:
                cursor.execute(
                    """
                    INSERT INTO aquila_product_modules (
                        module_id, product_id, sequence, name, description, priority, status
                    ) VALUES (%s, %s, %s, %s, %s, %s, 'planned')
                    """,
                    (str(uuid4()), product_id, sequence, name, description, priority),
                )
            except psycopg.errors.UniqueViolation as exc:
                raise ProductValidationError("A module with this name already exists") from exc
            cursor.execute(
                "UPDATE aquila_products SET updated_at = NOW() WHERE product_id = %s",
                (product_id,),
            )
            connection.commit()
    except (ProductNotFoundError, ProductValidationError):
        raise
    except ProductStoreError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ProductStoreError("Unable to create module") from exc
    return get_product_snapshot(product_id=product_id, organization_id=organization_id)


def update_module(
    *,
    product_id: str,
    module_id: str,
    organization_id: str,
    name: str | None = None,
    description: str | None = None,
    priority: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    if priority is not None and priority not in MODULE_PRIORITIES:
        raise ProductValidationError("Invalid module priority")
    if status is not None and status not in WORK_STATUSES:
        raise ProductValidationError("Invalid module status")
    updates: list[str] = []
    values: list[Any] = []
    if name is not None:
        clean_name = name.strip()
        if not clean_name:
            raise ProductValidationError("Module name is required")
        updates.append("name = %s")
        values.append(clean_name)
    if description is not None:
        updates.append("description = %s")
        values.append(description.strip())
    if priority is not None:
        updates.append("priority = %s")
        values.append(priority)
    if status is not None:
        updates.append("status = %s")
        values.append(status)
    if not updates:
        raise ProductValidationError("No module changes supplied")
    updates.append("updated_at = NOW()")
    values.extend([module_id, product_id, organization_id])
    try:
        with _connect(autocommit=False) as connection, connection.cursor() as cursor:
            try:
                cursor.execute(
                    f"""
                    UPDATE aquila_product_modules m
                    SET {', '.join(updates)}
                    FROM aquila_products p
                    WHERE m.module_id = %s
                      AND m.product_id = %s
                      AND p.product_id = m.product_id
                      AND p.organization_id = %s
                    RETURNING m.module_id
                    """,
                    values,
                )
            except psycopg.errors.UniqueViolation as exc:
                raise ProductValidationError("A module with this name already exists") from exc
            if not cursor.fetchone():
                raise ProductNotFoundError("Module not found")
            cursor.execute(
                "UPDATE aquila_products SET updated_at = NOW() WHERE product_id = %s",
                (product_id,),
            )
            connection.commit()
    except (ProductNotFoundError, ProductValidationError):
        raise
    except ProductStoreError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ProductStoreError("Unable to update module") from exc
    return get_product_snapshot(product_id=product_id, organization_id=organization_id)


def delete_module(*, product_id: str, module_id: str, organization_id: str) -> dict[str, Any]:
    try:
        with _connect(autocommit=False) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                DELETE FROM aquila_product_modules m
                USING aquila_products p
                WHERE m.module_id = %s
                  AND m.product_id = %s
                  AND p.product_id = m.product_id
                  AND p.organization_id = %s
                RETURNING m.module_id
                """,
                (module_id, product_id, organization_id),
            )
            if not cursor.fetchone():
                raise ProductNotFoundError("Module not found")
            cursor.execute(
                "UPDATE aquila_products SET updated_at = NOW() WHERE product_id = %s",
                (product_id,),
            )
            connection.commit()
    except ProductNotFoundError:
        raise
    except ProductStoreError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ProductStoreError("Unable to delete module") from exc
    return get_product_snapshot(product_id=product_id, organization_id=organization_id)


def update_deliverable_status(
    *,
    product_id: str,
    deliverable_id: str,
    organization_id: str,
    status: str,
) -> dict[str, Any]:
    if status not in WORK_STATUSES:
        raise ProductValidationError("Invalid deliverable status")
    try:
        with _connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE aquila_product_deliverables d
                SET status = %s, updated_at = NOW()
                FROM aquila_products p
                WHERE d.deliverable_id = %s
                  AND d.product_id = %s
                  AND p.product_id = d.product_id
                  AND p.organization_id = %s
                RETURNING d.deliverable_id
                """,
                (status, deliverable_id, product_id, organization_id),
            )
            if not cursor.fetchone():
                raise ProductNotFoundError("Deliverable not found")
            cursor.execute(
                "UPDATE aquila_products SET updated_at = NOW() WHERE product_id = %s",
                (product_id,),
            )
    except ProductNotFoundError:
        raise
    except ProductStoreError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ProductStoreError("Unable to update deliverable") from exc
    return get_product_snapshot(product_id=product_id, organization_id=organization_id)
