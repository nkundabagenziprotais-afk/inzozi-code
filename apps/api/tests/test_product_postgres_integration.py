from __future__ import annotations

import os
from uuid import uuid4

import psycopg
import pytest

from app.core.config import get_settings
from app.product.store import (
    ProductNotFoundError,
    create_module,
    create_product,
    delete_module,
    ensure_product_schema,
    get_product_snapshot,
    list_products,
    update_deliverable_status,
    update_module,
)
from app.routes.products import ModuleSeedRequest, ProductCreateRequest, build_initial_product_plan

RUN_POSTGRES = os.getenv("RUN_POSTGRES_INTEGRATION") == "1"
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://inzozi_code:test-only@127.0.0.1:5432/inzozi_code",
)

pytestmark = pytest.mark.skipif(
    not RUN_POSTGRES,
    reason="Set RUN_POSTGRES_INTEGRATION=1 with Postgres available",
)


@pytest.fixture(autouse=True)
def _configure_database(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)
    get_settings.cache_clear()
    ensure_product_schema()
    with psycopg.connect(DATABASE_URL, autocommit=True) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            TRUNCATE
                aquila_product_dependencies,
                aquila_product_deliverables,
                aquila_product_modules,
                aquila_product_components,
                aquila_product_blueprints,
                aquila_products
            CASCADE
            """
        )
    yield
    get_settings.cache_clear()


def _create_sample_product(*, organization_id: str = "org-a") -> dict:
    payload = ProductCreateRequest(
        name="Operations Platform",
        concept=(
            "Create a shared operations platform for managers and field teams with a web "
            "application, Android application, reporting and controlled external integrations."
        ),
        target_users=["Manager", "Field Officer"],
        platforms=["web", "android_phone"],
        capabilities=[
            "database",
            "internal_api",
            "external_integrations",
            "reporting",
            "documentation",
            "monitoring",
        ],
        modules=[
            ModuleSeedRequest(
                name="Work Orders",
                description="Create, assign and close operational work orders.",
                priority="must_have",
            ),
            ModuleSeedRequest(
                name="Management Dashboard",
                description="Summarize operational performance for managers.",
                priority="should_have",
            ),
        ],
    )
    plan = build_initial_product_plan(payload)
    return create_product(
        organization_id=organization_id,
        owner_user_id=str(uuid4()),
        name=payload.name,
        summary=payload.concept[:500],
        concept=payload.concept,
        target_users=payload.target_users,
        platforms=payload.platforms,
        capabilities=payload.capabilities,
        constraints=payload.constraints,
        components=plan["components"],
        modules=plan["modules"],
        deliverables=plan["deliverables"],
        dependencies=plan["dependencies"],
    )


def test_product_state_is_durable_and_organization_scoped():
    product = _create_sample_product()

    assert product["organization_id"] == "org-a"
    assert product["blueprint"]["platforms"] == ["web", "android_phone"]
    assert len(product["components"]) >= 8
    assert len(product["modules"]) == 2
    assert len(product["deliverables"]) >= 8
    assert len(product["dependencies"]) >= 2
    assert product["module_progress"] == {
        "total": 2,
        "complete": 0,
        "pending": 2,
        "blocked": 0,
        "progress_percent": 0,
        "priorities": {
            "must_have": {"total": 1, "complete": 0},
            "should_have": {"total": 1, "complete": 0},
            "good_to_have": {"total": 0, "complete": 0},
        },
    }

    org_a = list_products(organization_id="org-a")
    org_b = list_products(organization_id="org-b")
    assert [item["product_id"] for item in org_a] == [product["product_id"]]
    assert org_b == []

    with pytest.raises(ProductNotFoundError):
        get_product_snapshot(product_id=product["product_id"], organization_id="org-b")


def test_solution_module_lifecycle_tracks_priority_progress_and_scope():
    product = _create_sample_product()
    work_orders = next(item for item in product["modules"] if item["name"] == "Work Orders")

    updated = update_module(
        product_id=product["product_id"],
        module_id=work_orders["module_id"],
        organization_id="org-a",
        status="complete",
    )
    assert updated["module_progress"]["complete"] == 1
    assert updated["module_progress"]["pending"] == 1
    assert updated["module_progress"]["progress_percent"] == 50
    assert updated["module_progress"]["priorities"]["must_have"]["complete"] == 1

    updated = create_module(
        product_id=product["product_id"],
        organization_id="org-a",
        name="Mobile Inspections",
        description="Capture field inspection evidence.",
        priority="good_to_have",
    )
    assert updated["module_progress"]["total"] == 3
    inspection = next(item for item in updated["modules"] if item["name"] == "Mobile Inspections")
    assert inspection["sequence"] == 3
    assert inspection["priority"] == "good_to_have"

    updated = update_module(
        product_id=product["product_id"],
        module_id=inspection["module_id"],
        organization_id="org-a",
        priority="should_have",
        status="blocked",
    )
    inspection = next(item for item in updated["modules"] if item["module_id"] == inspection["module_id"])
    assert inspection["priority"] == "should_have"
    assert inspection["status"] == "blocked"
    assert updated["module_progress"]["blocked"] == 1

    with pytest.raises(ProductNotFoundError):
        update_module(
            product_id=product["product_id"],
            module_id=inspection["module_id"],
            organization_id="org-b",
            status="complete",
        )

    updated = delete_module(
        product_id=product["product_id"],
        module_id=inspection["module_id"],
        organization_id="org-a",
    )
    assert updated["module_progress"]["total"] == 2
    assert all(item["module_id"] != inspection["module_id"] for item in updated["modules"])


def test_deliverable_progress_updates_without_cross_product_mutation():
    first = _create_sample_product(organization_id="org-a")
    second = _create_sample_product(organization_id="org-b")

    target = next(item for item in first["deliverables"] if item["status"] == "in_progress")
    before = first["progress_percent"]
    updated = update_deliverable_status(
        product_id=first["product_id"],
        deliverable_id=target["deliverable_id"],
        organization_id="org-a",
        status="complete",
    )

    assert updated["progress_percent"] > before
    changed = next(item for item in updated["deliverables"] if item["deliverable_id"] == target["deliverable_id"])
    assert changed["status"] == "complete"

    untouched = get_product_snapshot(product_id=second["product_id"], organization_id="org-b")
    assert untouched["progress_percent"] == second["progress_percent"]

    with pytest.raises(ProductNotFoundError):
        update_deliverable_status(
            product_id=first["product_id"],
            deliverable_id=target["deliverable_id"],
            organization_id="org-b",
            status="blocked",
        )
