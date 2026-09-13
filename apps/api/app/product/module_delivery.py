from __future__ import annotations

from typing import Any
from uuid import uuid4

from app.product.engineering_sync import (
    EngineeringSyncError,
    _assert_child_belongs_to_product,
    _assert_product_access,
    _connect,
    _read_summary,
    ensure_engineering_sync_schema,
)
from app.product.store import (
    ProductNotFoundError,
    ProductStoreError,
    ProductValidationError,
)


def create_module_deliverable(
    *,
    product_id: str,
    organization_id: str,
    module_id: str,
    title: str,
    description: str = "",
) -> dict[str, Any]:
    """Create one planned deliverable and bind it to an intended module atomically."""

    ensure_engineering_sync_schema()
    clean_title = title.strip()
    clean_description = description.strip()
    if len(clean_title) < 2:
        raise ProductValidationError("Deliverable title is required")
    if len(clean_title) > 200:
        raise ProductValidationError("Deliverable title is too long")
    if len(clean_description) > 4000:
        raise ProductValidationError("Deliverable description is too long")

    try:
        with _connect(autocommit=False) as connection, connection.cursor() as cursor:
            _assert_product_access(cursor, product_id=product_id, organization_id=organization_id)
            _assert_child_belongs_to_product(
                cursor,
                product_id=product_id,
                child_id=module_id,
                table="aquila_product_modules",
                id_column="module_id",
                label="Module",
            )

            # Serialize sequence allocation per product so two concurrent planning
            # actions cannot receive the same product-level deliverable sequence.
            cursor.execute(
                "SELECT product_id FROM aquila_products WHERE product_id = %s FOR UPDATE",
                (product_id,),
            )
            cursor.execute(
                """
                SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence
                FROM aquila_product_deliverables
                WHERE product_id = %s
                """,
                (product_id,),
            )
            sequence = int(cursor.fetchone()["next_sequence"])
            deliverable_id = str(uuid4())

            cursor.execute(
                """
                INSERT INTO aquila_product_deliverables (
                    deliverable_id, product_id, component_id, sequence,
                    title, description, status
                ) VALUES (%s, %s, NULL, %s, %s, %s, 'planned')
                """,
                (
                    deliverable_id,
                    product_id,
                    sequence,
                    clean_title,
                    clean_description,
                ),
            )
            cursor.execute(
                """
                INSERT INTO aquila_product_module_deliverables (
                    assignment_id, product_id, module_id, deliverable_id
                ) VALUES (%s, %s, %s, %s)
                """,
                (str(uuid4()), product_id, module_id, deliverable_id),
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
        raise EngineeringSyncError("Unable to create module deliverable") from exc
