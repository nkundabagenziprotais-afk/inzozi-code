from __future__ import annotations

import os
from uuid import uuid4

import psycopg
import pytest

from app.core.config import get_settings
from app.main import create_app
from app.product.engineering_sync import (
    ensure_engineering_sync_schema,
    get_engineering_summary,
    record_engineering_event,
    update_engineering_binding,
)
from app.product.store import (
    ProductNotFoundError,
    ProductValidationError,
    create_product,
    ensure_product_schema,
    get_product_snapshot,
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
    assert "get" in paths["/v1/products/{product_id}/engineering"]
    assert "put" in paths["/v1/products/{product_id}/engineering/binding"]
    assert "post" in paths["/v1/products/{product_id}/engineering/events"]


def test_binding_and_engineering_evidence_update_real_progress_without_false_completion():
    solution = _create_solution()
    engineering_module = next(item for item in solution["modules"] if item["name"] == "Engineering Space")
    planned_deliverable = next(item for item in solution["deliverables"] if item["status"] == "planned")

    initial = get_engineering_summary(product_id=solution["product_id"], organization_id="org-a")
    assert initial["binding"] is None
    assert initial["evidence_count"] == 0
    assert initial["sync_health"] == "not_linked"

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
    assert bound["sync_health"] == "ready"

    active = record_engineering_event(
        product_id=solution["product_id"],
        organization_id="org-a",
        event_type="workspace_opened",
        status="success",
        summary="Guarded engineering workspace opened.",
        evidence={"workspace_id": "workspace-123"},
    )
    assert active["evidence_count"] == 1
    assert active["sync_health"] == "active"

    refreshed = get_product_snapshot(product_id=solution["product_id"], organization_id="org-a")
    module = next(item for item in refreshed["modules"] if item["module_id"] == engineering_module["module_id"])
    deliverable = next(
        item for item in refreshed["deliverables"]
        if item["deliverable_id"] == planned_deliverable["deliverable_id"]
    )
    assert module["status"] == "in_progress"
    assert deliverable["status"] == "in_progress"

    after_commit = record_engineering_event(
        product_id=solution["product_id"],
        organization_id="org-a",
        event_type="commit_created",
        status="success",
        summary="Reviewed local commit created.",
        evidence={"commit_sha": "abc123"},
    )
    assert after_commit["evidence_count"] == 2

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

    attention = record_engineering_event(
        product_id=solution["product_id"],
        organization_id="org-a",
        event_type="command_completed",
        status="failure",
        summary="Test command failed.",
        evidence={"action": "test", "exit_code": 1},
    )
    assert attention["sync_health"] == "attention"
    assert attention["evidence_count"] == 3


def test_engineering_sync_is_organization_scoped_and_rejects_foreign_children():
    first = _create_solution(organization_id="org-a")
    second = _create_solution(organization_id="org-b")
    foreign_module = second["modules"][0]

    with pytest.raises(ProductNotFoundError):
        get_engineering_summary(product_id=first["product_id"], organization_id="org-b")

    with pytest.raises(ProductValidationError):
        update_engineering_binding(
            product_id=first["product_id"],
            organization_id="org-a",
            changes={"module_id": foreign_module["module_id"]},
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
