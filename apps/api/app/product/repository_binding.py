from __future__ import annotations

from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from app.core.config import get_settings
from app.product.store import (
    ProductNotFoundError,
    ProductStoreError,
    ProductValidationError,
    ensure_product_schema,
)


class RepositoryBindingError(ProductStoreError):
    """Persistent Product Control repository binding failed."""


def _connect(*, autocommit: bool = True):
    try:
        return psycopg.connect(
            get_settings().database_url,
            autocommit=autocommit,
            row_factory=dict_row,
        )
    except Exception as exc:  # noqa: BLE001
        raise RepositoryBindingError("Repository binding store unavailable") from exc


def normalize_repository_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    if normalized.endswith(".git"):
        normalized = normalized[:-4]
    parsed = urlparse(normalized)
    if parsed.scheme != "https" or not parsed.netloc or not parsed.path.strip("/"):
        raise ProductValidationError("Repository URL must be a valid HTTPS repository URL")
    return normalized


def ensure_repository_binding_schema() -> None:
    ensure_product_schema()
    with _connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS aquila_product_repositories (
                repository_id UUID PRIMARY KEY,
                product_id UUID NOT NULL
                    REFERENCES aquila_products(product_id) ON DELETE CASCADE,
                repository_url TEXT NOT NULL,
                repository_ref VARCHAR(255) NULL,
                workspace_id VARCHAR(160) NULL,
                is_default BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT aquila_product_repository_unique
                    UNIQUE (product_id, repository_url)
            )
            """
        )
        cursor.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS aquila_product_repository_default_idx
            ON aquila_product_repositories (product_id)
            WHERE is_default
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS aquila_product_module_repositories (
                binding_id UUID PRIMARY KEY,
                product_id UUID NOT NULL
                    REFERENCES aquila_products(product_id) ON DELETE CASCADE,
                module_id UUID NOT NULL
                    REFERENCES aquila_product_modules(module_id) ON DELETE CASCADE,
                repository_id UUID NOT NULL
                    REFERENCES aquila_product_repositories(repository_id) ON DELETE CASCADE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT aquila_product_module_repository_unique
                    UNIQUE (product_id, module_id)
            )
            """
        )


def _assert_product_access(cursor, *, product_id: str, organization_id: str) -> None:
    cursor.execute(
        "SELECT 1 FROM aquila_products WHERE product_id = %s AND organization_id = %s",
        (product_id, organization_id),
    )
    if not cursor.fetchone():
        raise ProductNotFoundError("Product not found")


def _assert_module(cursor, *, product_id: str, module_id: str) -> None:
    cursor.execute(
        "SELECT 1 FROM aquila_product_modules WHERE product_id = %s AND module_id = %s",
        (product_id, module_id),
    )
    if not cursor.fetchone():
        raise ProductValidationError("Module does not belong to this product")


def _assert_repository(cursor, *, product_id: str, repository_id: str) -> None:
    cursor.execute(
        "SELECT 1 FROM aquila_product_repositories WHERE product_id = %s AND repository_id = %s",
        (product_id, repository_id),
    )
    if not cursor.fetchone():
        raise ProductValidationError("Repository does not belong to this product")


def _state(cursor, *, product_id: str, module_id: str | None = None) -> dict[str, Any]:
    cursor.execute(
        """
        SELECT repository_id, repository_url, repository_ref, workspace_id,
               is_default, created_at, updated_at
        FROM aquila_product_repositories
        WHERE product_id = %s
        ORDER BY is_default DESC, created_at, repository_id
        """,
        (product_id,),
    )
    repositories = [dict(row) for row in cursor.fetchall()]

    cursor.execute(
        """
        SELECT mr.module_id, m.name AS module_name, mr.repository_id,
               r.repository_url, r.repository_ref, r.workspace_id,
               mr.updated_at
        FROM aquila_product_module_repositories mr
        JOIN aquila_product_modules m
          ON m.product_id = mr.product_id AND m.module_id = mr.module_id
        JOIN aquila_product_repositories r
          ON r.product_id = mr.product_id AND r.repository_id = mr.repository_id
        WHERE mr.product_id = %s
        ORDER BY m.sequence, m.created_at
        """,
        (product_id,),
    )
    module_bindings = [dict(row) for row in cursor.fetchall()]

    resolved = None
    resolution = "none"
    if module_id:
        resolved = next((item for item in module_bindings if str(item["module_id"]) == str(module_id)), None)
        if resolved:
            resolution = "module_override"
    if resolved is None:
        resolved = next((item for item in repositories if item["is_default"]), None)
        if resolved:
            resolution = "product_default"

    def clean(item: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value.isoformat() if hasattr(value, "isoformat") else str(value) if value.__class__.__name__ == "UUID" else value
            for key, value in item.items()
        }

    return {
        "repositories": [clean(item) for item in repositories],
        "module_repository_bindings": [clean(item) for item in module_bindings],
        "resolved_repository": clean(resolved) if resolved else None,
        "repository_resolution": resolution,
    }


def get_repository_state(*, product_id: str, organization_id: str, module_id: str | None = None) -> dict[str, Any]:
    ensure_repository_binding_schema()
    try:
        with _connect() as connection, connection.cursor() as cursor:
            _assert_product_access(cursor, product_id=product_id, organization_id=organization_id)
            if module_id:
                _assert_module(cursor, product_id=product_id, module_id=module_id)
            return _state(cursor, product_id=product_id, module_id=module_id)
    except (ProductNotFoundError, ProductValidationError):
        raise
    except ProductStoreError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise RepositoryBindingError("Unable to read repository bindings") from exc


def save_product_repository(
    *,
    product_id: str,
    organization_id: str,
    repository_url: str,
    repository_ref: str | None,
    make_default: bool,
) -> dict[str, Any]:
    ensure_repository_binding_schema()
    normalized = normalize_repository_url(repository_url)
    ref = (repository_ref or "").strip() or None
    try:
        with _connect(autocommit=False) as connection, connection.cursor() as cursor:
            _assert_product_access(cursor, product_id=product_id, organization_id=organization_id)
            cursor.execute(
                "SELECT 1 FROM aquila_product_repositories WHERE product_id = %s AND is_default",
                (product_id,),
            )
            should_default = make_default or not bool(cursor.fetchone())
            if should_default:
                cursor.execute(
                    "UPDATE aquila_product_repositories SET is_default = FALSE, updated_at = NOW() WHERE product_id = %s",
                    (product_id,),
                )
            cursor.execute(
                """
                INSERT INTO aquila_product_repositories (
                    repository_id, product_id, repository_url, repository_ref, is_default
                ) VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (product_id, repository_url)
                DO UPDATE SET repository_ref = EXCLUDED.repository_ref,
                              is_default = EXCLUDED.is_default OR aquila_product_repositories.is_default,
                              updated_at = NOW()
                """,
                (str(uuid4()), product_id, normalized, ref, should_default),
            )
            cursor.execute("UPDATE aquila_products SET updated_at = NOW() WHERE product_id = %s", (product_id,))
            connection.commit()
            return _state(cursor, product_id=product_id)
    except (ProductNotFoundError, ProductValidationError):
        raise
    except ProductStoreError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise RepositoryBindingError("Unable to save product repository") from exc


def bind_module_repository(
    *,
    product_id: str,
    organization_id: str,
    module_id: str,
    repository_id: str | None,
) -> dict[str, Any]:
    ensure_repository_binding_schema()
    try:
        with _connect(autocommit=False) as connection, connection.cursor() as cursor:
            _assert_product_access(cursor, product_id=product_id, organization_id=organization_id)
            _assert_module(cursor, product_id=product_id, module_id=module_id)
            if repository_id is None:
                cursor.execute(
                    "DELETE FROM aquila_product_module_repositories WHERE product_id = %s AND module_id = %s",
                    (product_id, module_id),
                )
            else:
                _assert_repository(cursor, product_id=product_id, repository_id=repository_id)
                cursor.execute(
                    """
                    INSERT INTO aquila_product_module_repositories (
                        binding_id, product_id, module_id, repository_id
                    ) VALUES (%s, %s, %s, %s)
                    ON CONFLICT (product_id, module_id)
                    DO UPDATE SET repository_id = EXCLUDED.repository_id, updated_at = NOW()
                    """,
                    (str(uuid4()), product_id, module_id, repository_id),
                )
            cursor.execute("UPDATE aquila_products SET updated_at = NOW() WHERE product_id = %s", (product_id,))
            connection.commit()
            return _state(cursor, product_id=product_id, module_id=module_id)
    except (ProductNotFoundError, ProductValidationError):
        raise
    except ProductStoreError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise RepositoryBindingError("Unable to bind module repository") from exc


def remember_repository_workspace(
    *,
    product_id: str,
    organization_id: str,
    repository_url: str,
    repository_ref: str | None,
    workspace_id: str,
    module_id: str | None = None,
) -> dict[str, Any]:
    """Remember the last guarded workspace without changing module inheritance semantics."""

    ensure_repository_binding_schema()
    normalized = normalize_repository_url(repository_url)
    ref = (repository_ref or "").strip() or None
    try:
        with _connect(autocommit=False) as connection, connection.cursor() as cursor:
            _assert_product_access(cursor, product_id=product_id, organization_id=organization_id)
            if module_id:
                _assert_module(cursor, product_id=product_id, module_id=module_id)
            cursor.execute(
                "SELECT repository_id FROM aquila_product_repositories WHERE product_id = %s AND repository_url = %s",
                (product_id, normalized),
            )
            row = cursor.fetchone()
            if row:
                repository_id = str(row["repository_id"])
                cursor.execute(
                    """
                    UPDATE aquila_product_repositories
                    SET repository_ref = %s, workspace_id = %s, updated_at = NOW()
                    WHERE product_id = %s AND repository_id = %s
                    """,
                    (ref, workspace_id, product_id, repository_id),
                )
            else:
                cursor.execute(
                    "SELECT 1 FROM aquila_product_repositories WHERE product_id = %s AND is_default",
                    (product_id,),
                )
                is_default = not bool(cursor.fetchone())
                repository_id = str(uuid4())
                cursor.execute(
                    """
                    INSERT INTO aquila_product_repositories (
                        repository_id, product_id, repository_url, repository_ref, workspace_id, is_default
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (repository_id, product_id, normalized, ref, workspace_id, is_default),
                )
            connection.commit()
            return _state(cursor, product_id=product_id, module_id=module_id)
    except (ProductNotFoundError, ProductValidationError):
        raise
    except ProductStoreError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise RepositoryBindingError("Unable to remember repository workspace") from exc
