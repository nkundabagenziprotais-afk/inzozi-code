from __future__ import annotations

import os
from uuid import uuid4

import psycopg
import pytest

from app.core.config import get_settings
from app.main import create_app
from app.product.engineering_sync import (
    assign_deliverable_to_module,
    ensure_engineering_sync_schema,
    get_engineering_summary,
    record_engineering_event,
    update_engineering_binding,
)
from app.product.module_delivery import create_module_deliverable
from app.product.store import (
    ProductNotFoundError,
    ProductValidationError,
    create_product,
    ensure_product_schema,
    get_product_snapshot,
    update_deliverable_status,
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
    ensure_engineering_sync_schema()
    with psycopg.connect(DATABASE_URL, autocommit=True) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            TRUNCATE
                aquila_product_engineering_events,
                aquila_product_engineering_bindings,
                aquila_product_module_deliverables,
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


def _create_solution(*, organization_id: str = "org-a") -> dict:
    payload = ProductCreateRequest(
        name="Inzozi AI-Coding",
        concept=(
            "Deliver one synchronized AI software delivery solution where Project Control owns "
            "intent and Engineering Space executes repository, test and Git work with evidence."
        ),
        target_users=["Product Owner", "Engineer"],
        platforms=["web"],
        capabilities=["database", "internal_api", "documentation", "monitoring"],
        modules=[
            ModuleSeedRequest(
                name="Project Control Center",
                description="Own solution intent, priorities, deliverables and progress.",
                priority="must_have",
            ),
            ModuleSeedRequest(
                name="Engineering Space",
                description="Execute repository, code, test and Git work.",
                priority="must_have",
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


def test_engineering_sync_api_contract_is_exposed():
    paths = create_app().openapi()["paths"]
    assert "/v1/products/{product_id}/engineering" in paths
    assert "/v1/products/{product_id}/engineering/binding" in paths
    assert "/v1/products/{product_id}/engineering/events" in paths
    assert "/v1/products/{product_id}/engineering/deliverables/{deliverable_id}/module" in paths
    assert "/v1/products/{product_id}/engineering/modules/{module_id}/deliverables" in paths
    assert "get" in paths["/v1/products/{product_id}/engineering"]
    assert "put" in paths["/v1/products/{product_id}/engineering/binding"]
    assert "post" in paths["/v1/products/{product_id}/engineering/events"]
    assert "put" in paths["/v1/products/{product_id}/engineering/deliverables/{deliverable_id}/module"]
    assert "post" in paths["/v1/products/{product_id}/engineering/modules/{module_id}/deliverables"]


def test_module_deliverable_creation_is_atomic_sequenced_and_handoff_ready():
    solution = _create_solution()
    module = solution["modules"][0]
    max_sequence = max(item["sequence"] for item in solution["deliverables"])

    created = create_module_deliverable(
        product_id=solution["product_id"],
        organization_id="org-a",
        module_id=module["module_id"],
        title="Product Control module work breakdown",
        description="Create and synchronize module-specific deliverables into Engineering Space.",
    )

    deliverable = next(
        item for item in created["delivery_deliverables"]
        if item["title"] == "Product Control module work breakdown"
    )
    assert deliverable["sequence"] == max_sequence + 1
    assert deliverable["status"] == "planned"
    assert deliverable["module_id"] == module["module_id"]
    assert deliverable["module_name"] == module["name"]

    module_progress = next(
        item for item in created["module_delivery"]
        if item["module_id"] == module["module_id"]
    )
    assert module_progress["deliverable_total"] == 1
    assert module_progress["deliverable_complete"] == 0
    assert module_progress["progress_percent"] == 0

    snapshot = get_product_snapshot(product_id=solution["product_id"], organization_id="org-a")
    persisted = next(
        item for item in snapshot["deliverables"]
        if item["deliverable_id"] == deliverable["deliverable_id"]
    )
    assert persisted["sequence"] == max_sequence + 1
    assert persisted["status"] == "planned"

    bound = update_engineering_binding(
        product_id=solution["product_id"],
        organization_id="org-a",
        changes={
            "module_id": module["module_id"],
            "deliverable_id": deliverable["deliverable_id"],
        },
    )
    assert bound["binding"]["module_id"] == module["module_id"]
    assert bound["binding"]["deliverable_id"] == deliverable["deliverable_id"]

    record_engineering_event(
        product_id=solution["product_id"],
        organization_id="org-a",
        event_type="engineering_opened",
        status="info",
        summary="Engineering Space opened for the planned module deliverable.",
        evidence={},
    )
    refreshed = get_product_snapshot(product_id=solution["product_id"], organization_id="org-a")
    refreshed_deliverable = next(
        item for item in refreshed["deliverables"]
        if item["deliverable_id"] == deliverable["deliverable_id"]
    )
    refreshed_module = next(
        item for item in refreshed["modules"]
        if item["module_id"] == module["module_id"]
    )
    assert refreshed_deliverable["status"] == "in_progress"
    assert refreshed_module["status"] == "in_progress"
    assert refreshed_deliverable["status"] != "complete"
    assert refreshed_module["status"] != "complete"


def test_module_deliverable_creation_is_organization_and_module_scoped():
    first = _create_solution(organization_id="org-a")
    second = _create_solution(organization_id="org-b")

    with pytest.raises(ProductNotFoundError):
        create_module_deliverable(
            product_id=first["product_id"],
            organization_id="org-b",
            module_id=first["modules"][0]["module_id"],
            title="Unauthorized work",
        )

    with pytest.raises(ProductValidationError):
        create_module_deliverable(
            product_id=first["product_id"],
            organization_id="org-a",
            module_id=second["modules"][0]["module_id"],
            title="Foreign module work",
        )

    summary = get_engineering_summary(product_id=first["product_id"], organization_id="org-a")
    assert all(item["title"] != "Unauthorized work" for item in summary["delivery_deliverables"])
    assert all(item["title"] != "Foreign module work" for item in summary["delivery_deliverables"])


def test_module_delivery_binding_and_progress_are_persistent_and_acceptance_driven():
    solution = _create_solution()
    engineering_module = next(item for item in solution["modules"] if item["name"] == "Engineering Space")
    planned_deliverable = next(item for item in solution["deliverables"] if item["status"] == "planned")

    initial = get_engineering_summary(product_id=solution["product_id"], organization_id="org-a")
    assert initial["binding"] is None
    assert initial["module_delivery_summary"]["linked_deliverables_total"] == 0
    assert initial["module_proposals"]

    with pytest.raises(ProductValidationError):
        update_engineering_binding(
            product_id=solution["product_id"],
            organization_id="org-a",
            changes={
                "module_id": engineering_module["module_id"],
                "deliverable_id": planned_deliverable["deliverable_id"],
            },
        )

    assigned = assign_deliverable_to_module(
        product_id=solution["product_id"],
        organization_id="org-a",
        deliverable_id=planned_deliverable["deliverable_id"],
        module_id=engineering_module["module_id"],
    )
    delivery = next(
        item for item in assigned["delivery_deliverables"]
        if item["deliverable_id"] == planned_deliverable["deliverable_id"]
    )
    assert delivery["module_id"] == engineering_module["module_id"]
    module_progress = next(
        item for item in assigned["module_delivery"]
        if item["module_id"] == engineering_module["module_id"]
    )
    assert module_progress["deliverable_total"] == 1
    assert module_progress["deliverable_complete"] == 0
    assert module_progress["progress_percent"] == 0

    bound = update_engineering_binding(
        product_id=solution["product_id"],
        organization_id="org-a",
        changes={
            "module_id": engineering_module["module_id"],
            "deliverable_id": planned_deliverable["deliverable_id"],
            "repository_url": "https://github.com/nkundabagenziprotais-afk/inzozi-code",
            "workspace_id": "workspace-123",
            "repository_ref": "feature/synchronized-delivery",
        },
    )
    assert bound["binding"]["module_name"] == "Engineering Space"
    assert bound["binding"]["deliverable_title"] == planned_deliverable["title"]

    record_engineering_event(
        product_id=solution["product_id"],
        organization_id="org-a",
        event_type="commit_created",
        status="success",
        summary="Reviewed local commit created.",
        evidence={"commit_sha": "abc123"},
    )

    refreshed = get_product_snapshot(product_id=solution["product_id"], organization_id="org-a")
    module = next(item for item in refreshed["modules"] if item["module_id"] == engineering_module["module_id"])
    deliverable = next(
        item for item in refreshed["deliverables"]
        if item["deliverable_id"] == planned_deliverable["deliverable_id"]
    )
    assert module["status"] == "in_progress"
    assert deliverable["status"] == "in_progress"
    assert module["status"] != "complete"
    assert deliverable["status"] != "complete"

    completed = update_deliverable_status(
        product_id=solution["product_id"],
        deliverable_id=planned_deliverable["deliverable_id"],
        organization_id="org-a",
        status="complete",
    )
    assert next(
        item for item in completed["deliverables"]
        if item["deliverable_id"] == planned_deliverable["deliverable_id"]
    )["status"] == "complete"

    final_summary = get_engineering_summary(product_id=solution["product_id"], organization_id="org-a")
    module_progress = next(
        item for item in final_summary["module_delivery"]
        if item["module_id"] == engineering_module["module_id"]
    )
    assert module_progress["deliverable_complete"] == 1
    assert module_progress["progress_percent"] == 100
    assert module_progress["derived_status"] == "complete"
    assert final_summary["module_delivery_summary"]["progress_percent"] == 100


def test_module_delivery_reassignment_updates_engineering_context_and_can_be_cleared():
    solution = _create_solution()
    first_module, second_module = solution["modules"][:2]
    deliverable = next(item for item in solution["deliverables"] if item["status"] == "planned")

    assign_deliverable_to_module(
        product_id=solution["product_id"],
        organization_id="org-a",
        deliverable_id=deliverable["deliverable_id"],
        module_id=first_module["module_id"],
    )
    update_engineering_binding(
        product_id=solution["product_id"],
        organization_id="org-a",
        changes={
            "module_id": first_module["module_id"],
            "deliverable_id": deliverable["deliverable_id"],
        },
    )

    moved = assign_deliverable_to_module(
        product_id=solution["product_id"],
        organization_id="org-a",
        deliverable_id=deliverable["deliverable_id"],
        module_id=second_module["module_id"],
    )
    assert moved["binding"]["module_id"] == second_module["module_id"]
    assert moved["binding"]["deliverable_id"] == deliverable["deliverable_id"]

    cleared = assign_deliverable_to_module(
        product_id=solution["product_id"],
        organization_id="org-a",
        deliverable_id=deliverable["deliverable_id"],
        module_id=None,
    )
    assert cleared["binding"]["deliverable_id"] is None
    delivery = next(
        item for item in cleared["delivery_deliverables"]
        if item["deliverable_id"] == deliverable["deliverable_id"]
    )
    assert delivery["module_id"] is None


def test_engineering_sync_is_organization_scoped_and_rejects_foreign_children():
    first = _create_solution(organization_id="org-a")
    second = _create_solution(organization_id="org-b")
    foreign_module = second["modules"][0]
    first_deliverable = first["deliverables"][0]

    with pytest.raises(ProductNotFoundError):
        get_engineering_summary(product_id=first["product_id"], organization_id="org-b")

    with pytest.raises(ProductValidationError):
        update_engineering_binding(
            product_id=first["product_id"],
            organization_id="org-a",
            changes={"module_id": foreign_module["module_id"]},
        )

    with pytest.raises(ProductValidationError):
        assign_deliverable_to_module(
            product_id=first["product_id"],
            organization_id="org-a",
            deliverable_id=first_deliverable["deliverable_id"],
            module_id=foreign_module["module_id"],
        )

    with pytest.raises(ProductNotFoundError):
        record_engineering_event(
            product_id=first["product_id"],
            organization_id="org-b",
            event_type="engineering_opened",
            status="info",
            summary="Unauthorized cross-organization event.",
            evidence={},
        )
