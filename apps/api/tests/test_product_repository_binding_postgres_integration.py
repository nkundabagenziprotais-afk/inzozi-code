from __future__ import annotations

import os
from uuid import uuid4

import psycopg
import pytest

from app.core.config import get_settings
from app.main import create_app
from app.product.engineering_sync import ensure_engineering_sync_schema
from app.product.repository_binding import (
    bind_module_repository,
    ensure_repository_binding_schema,
    get_repository_state,
    normalize_repository_url,
    remember_repository_workspace,
    save_product_repository,
)
from app.product.store import (
    ProductNotFoundError,
    ProductValidationError,
    create_product,
    ensure_product_schema,
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
    ensure_repository_binding_schema()
    with psycopg.connect(DATABASE_URL, autocommit=True) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            TRUNCATE
                aquila_product_module_repositories,
                aquila_product_repositories,
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
        name="School Management Solution",
        concept="Manage a school from admissions through academic delivery and reporting.",
        target_users=["Administrator", "Teacher"],
        platforms=["web"],
        capabilities=["database", "internal_api"],
        modules=[
            ModuleSeedRequest(
                name="Academic Management",
                description="Manage subjects, classes, assessments and grades.",
                priority="must_have",
            ),
            ModuleSeedRequest(
                name="Fees & Finance",
                description="Manage billing and collections.",
                priority="must_have",
            ),
        ],
    )
    plan = build_initial_product_plan(payload)
    return create_product(
        organization_id=organization_id,
        owner_user_id=str(uuid4()),
        name=payload.name,
        summary=payload.concept,
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


def test_repository_binding_api_contract_is_exposed():
    paths = create_app().openapi()["paths"]
    repositories = "/v1/products/{product_id}/engineering/repositories"
    module_repository = "/v1/products/{product_id}/engineering/modules/{module_id}/repository"
    assert repositories in paths
    assert module_repository in paths
    assert "post" in paths[repositories]
    assert "put" in paths[module_repository]


def test_repository_url_normalization_is_stable():
    assert normalize_repository_url(" https://github.com/example/repo.git/ ") == "https://github.com/example/repo"
    with pytest.raises(ProductValidationError):
        normalize_repository_url("git@github.com:example/repo.git")


def test_product_default_module_override_and_workspace_memory_are_persistent():
    solution = _create_solution()
    academic, finance = solution["modules"][:2]

    first = save_product_repository(
        product_id=solution["product_id"],
        organization_id="org-a",
        repository_url="https://github.com/example/school-core.git",
        repository_ref="main",
        make_default=False,
    )
    assert len(first["repositories"]) == 1
    default_repo = first["repositories"][0]
    assert default_repo["is_default"] is True

    inherited = get_repository_state(
        product_id=solution["product_id"],
        organization_id="org-a",
        module_id=academic["module_id"],
    )
    assert inherited["repository_resolution"] == "product_default"
    assert inherited["resolved_repository"]["repository_id"] == default_repo["repository_id"]

    second = save_product_repository(
        product_id=solution["product_id"],
        organization_id="org-a",
        repository_url="https://github.com/example/school-finance",
        repository_ref="develop",
        make_default=False,
    )
    finance_repo = next(
        item for item in second["repositories"]
        if item["repository_url"] == "https://github.com/example/school-finance"
    )

    overridden = bind_module_repository(
        product_id=solution["product_id"],
        organization_id="org-a",
        module_id=finance["module_id"],
        repository_id=finance_repo["repository_id"],
    )
    assert overridden["repository_resolution"] == "module_override"
    assert overridden["resolved_repository"]["repository_id"] == finance_repo["repository_id"]

    remembered = remember_repository_workspace(
        product_id=solution["product_id"],
        organization_id="org-a",
        repository_url="https://github.com/example/school-finance.git",
        repository_ref="feature/fees",
        workspace_id="workspace-finance-123",
        module_id=finance["module_id"],
    )
    assert remembered["resolved_repository"]["workspace_id"] == "workspace-finance-123"
    assert remembered["resolved_repository"]["repository_ref"] == "feature/fees"

    still_inherited = get_repository_state(
        product_id=solution["product_id"],
        organization_id="org-a",
        module_id=academic["module_id"],
    )
    assert still_inherited["repository_resolution"] == "product_default"
    assert still_inherited["resolved_repository"]["repository_id"] == default_repo["repository_id"]


def test_repository_binding_is_organization_and_module_scoped():
    first = _create_solution(organization_id="org-a")
    second = _create_solution(organization_id="org-b")

    state = save_product_repository(
        product_id=first["product_id"],
        organization_id="org-a",
        repository_url="https://github.com/example/first",
        repository_ref="main",
        make_default=True,
    )
    repository_id = state["repositories"][0]["repository_id"]

    with pytest.raises(ProductNotFoundError):
        get_repository_state(
            product_id=first["product_id"],
            organization_id="org-b",
        )

    with pytest.raises(ProductValidationError):
        bind_module_repository(
            product_id=first["product_id"],
            organization_id="org-a",
            module_id=second["modules"][0]["module_id"],
            repository_id=repository_id,
        )

    foreign_state = save_product_repository(
        product_id=second["product_id"],
        organization_id="org-b",
        repository_url="https://github.com/example/second",
        repository_ref="main",
        make_default=True,
    )
    foreign_repository_id = foreign_state["repositories"][0]["repository_id"]

    with pytest.raises(ProductValidationError):
        bind_module_repository(
            product_id=first["product_id"],
            organization_id="org-a",
            module_id=first["modules"][0]["module_id"],
            repository_id=foreign_repository_id,
        )
