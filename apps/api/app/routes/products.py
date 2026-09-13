from __future__ import annotations

from functools import lru_cache
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.product.store import (
    ProductNotFoundError,
    ProductStoreError,
    ProductValidationError,
    create_product,
    ensure_product_schema,
    get_product_snapshot,
    list_products,
    update_deliverable_status,
)
from app.security.auth import AuthPrincipal, require_permission

router = APIRouter(prefix="/v1/products", tags=["products"])

PlatformTarget = Literal[
    "web",
    "windows",
    "macos",
    "android_phone",
    "ios_phone",
    "android_tablet",
    "ipad",
    "wearable",
]
CapabilityTarget = Literal[
    "database",
    "internal_api",
    "external_integrations",
    "reporting",
    "notifications",
    "offline",
    "documentation",
    "monitoring",
]
WorkStatus = Literal["planned", "in_progress", "blocked", "complete"]

PLATFORM_LABELS: dict[str, str] = {
    "web": "Web Application",
    "windows": "Windows Desktop",
    "macos": "macOS Desktop",
    "android_phone": "Android Phone",
    "ios_phone": "iPhone",
    "android_tablet": "Android Tablet",
    "ipad": "iPad",
    "wearable": "Wearable / Smart Watch",
}


class ProductCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    concept: str = Field(min_length=20, max_length=8000)
    target_users: list[str] = Field(default_factory=list, max_length=30)
    platforms: list[PlatformTarget] = Field(default_factory=lambda: ["web"])
    capabilities: list[CapabilityTarget] = Field(
        default_factory=lambda: ["database", "internal_api", "documentation", "monitoring"]
    )
    constraints: list[str] = Field(default_factory=list, max_length=30)


class DeliverableStatusRequest(BaseModel):
    status: WorkStatus


def _principal(request: Request) -> AuthPrincipal:
    principal = getattr(request.state, "principal", None)
    if not isinstance(principal, AuthPrincipal):
        raise HTTPException(status_code=401, detail="Authentication required")
    return principal


@lru_cache(maxsize=1)
def _ensure_product_control_ready() -> None:
    """Initialize Product Control lazily outside database-mode staging startup.

    Database-identity staging still initializes this schema fail-closed during
    application startup. Local/auth-disabled development remains lightweight and
    only requires PostgreSQL when Product Control is actually used.
    """
    ensure_product_schema()


async def _ensure_store() -> None:
    try:
        await run_in_threadpool(_ensure_product_control_ready)
    except ProductStoreError as exc:
        raise HTTPException(status_code=503, detail="Product store unavailable") from exc


def _component(
    component_key: str,
    kind: str,
    name: str,
    description: str,
    *,
    platform: str | None = None,
) -> dict:
    return {
        "component_key": component_key,
        "kind": kind,
        "name": name,
        "description": description,
        "platform": platform,
        "status": "planned",
    }


def build_initial_product_plan(payload: ProductCreateRequest) -> dict[str, list[dict]]:
    """Translate non-technical discovery choices into a first durable system plan."""
    components: list[dict] = [
        _component(
            "solution-architecture",
            "solution",
            "Solution Architecture",
            "Defines the system boundaries, components, interfaces, environments and delivery sequence.",
        ),
        _component(
            "security-governance",
            "security",
            "Security & Governance",
            "Identity, roles, approvals, auditability, secrets and release controls shared by the product.",
        ),
    ]

    capability_set = set(payload.capabilities)
    if "database" in capability_set:
        components.append(
            _component(
                "data-platform",
                "data",
                "Data Platform",
                "Durable product data, schema, migrations, backup and recovery requirements.",
            )
        )
    if "internal_api" in capability_set:
        components.append(
            _component(
                "application-api",
                "api",
                "Application APIs",
                "Internal service contracts used by the selected user experiences and product modules.",
            )
        )
    if "external_integrations" in capability_set:
        components.append(
            _component(
                "external-integrations",
                "integration",
                "External Integrations",
                "Third-party and partner APIs, credentials, mappings, retries, sandbox and production endpoints.",
            )
        )
    if "reporting" in capability_set:
        components.append(
            _component(
                "reporting-analytics",
                "module",
                "Reporting & Analytics",
                "Operational and management reports, dashboards and export requirements.",
            )
        )
    if "notifications" in capability_set:
        components.append(
            _component(
                "notifications",
                "integration",
                "Notifications",
                "Email, SMS, push or in-product notification capabilities.",
            )
        )
    if "offline" in capability_set:
        components.append(
            _component(
                "offline-sync",
                "platform",
                "Offline & Synchronization",
                "Offline behavior, conflict handling, local persistence and synchronization rules.",
            )
        )

    for platform in payload.platforms:
        label = PLATFORM_LABELS[platform]
        components.append(
            _component(
                f"experience-{platform}",
                "experience",
                label,
                f"User experience, navigation, accessibility, device behavior and release needs for {label}.",
                platform=platform,
            )
        )

    if "documentation" in capability_set:
        components.append(
            _component(
                "documentation",
                "documentation",
                "Documentation & Training",
                "Living user, administrator, technical and training documentation tied to certified features.",
            )
        )
    if "monitoring" in capability_set:
        components.append(
            _component(
                "operations",
                "operations",
                "Operations & Reliability",
                "Environment health, monitoring, alerting, backup, recovery and operational readiness.",
            )
        )

    deliverables: list[dict] = [
        {
            "sequence": 1,
            "title": "Product blueprint and discovery",
            "description": "Capture the product concept, users, target platforms, capabilities and initial scope.",
            "status": "complete",
        },
        {
            "sequence": 2,
            "title": "Solution architecture",
            "description": "Confirm component boundaries, dependencies, environments and engineering approach.",
            "component_key": "solution-architecture",
            "status": "in_progress",
        },
        {
            "sequence": 3,
            "title": "Security and governance foundation",
            "description": "Define identity, roles, approvals, audit and protection requirements.",
            "component_key": "security-governance",
            "status": "planned",
        },
    ]

    delivery_components = [
        item
        for item in components
        if item["component_key"] not in {"solution-architecture", "security-governance"}
    ]
    sequence = 4
    for component in delivery_components:
        deliverables.append(
            {
                "sequence": sequence,
                "title": component["name"],
                "description": f"Design, build, test and prepare {component['name']} for integration.",
                "component_key": component["component_key"],
                "status": "planned",
            }
        )
        sequence += 1

    deliverables.extend(
        [
            {
                "sequence": sequence,
                "title": "Integrated quality and regression testing",
                "description": "Validate the assembled solution across components, roles, platforms and dependencies.",
                "status": "planned",
            },
            {
                "sequence": sequence + 1,
                "title": "User acceptance and certification",
                "description": "Capture UAT evidence and certify each required platform/component before release.",
                "status": "planned",
            },
            {
                "sequence": sequence + 2,
                "title": "Environment and release readiness",
                "description": "Prepare testing/staging/production environments, release impact review and rollback evidence.",
                "status": "planned",
            },
        ]
    )

    component_keys = {item["component_key"] for item in components}
    dependencies: list[dict] = []
    for item in components:
        key = item["component_key"]
        if key != "solution-architecture":
            dependencies.append(
                {
                    "upstream_key": "solution-architecture",
                    "downstream_key": key,
                    "relationship": "defines",
                    "impact_note": "Architecture changes require this component to be reviewed.",
                }
            )

    if "application-api" in component_keys:
        if "data-platform" in component_keys:
            dependencies.append(
                {
                    "upstream_key": "data-platform",
                    "downstream_key": "application-api",
                    "relationship": "serves",
                    "impact_note": "Data-contract changes may require API compatibility review.",
                }
            )
        for item in components:
            if item["kind"] == "experience":
                dependencies.append(
                    {
                        "upstream_key": "application-api",
                        "downstream_key": item["component_key"],
                        "relationship": "supports",
                        "impact_note": "API changes may affect this user experience and require regression testing.",
                    }
                )

    return {
        "components": components,
        "deliverables": deliverables,
        "dependencies": dependencies,
    }


@router.get("")
async def products(request: Request) -> dict:
    principal = _principal(request)
    require_permission(request, "workspace:read")
    await _ensure_store()
    try:
        items = await run_in_threadpool(list_products, organization_id=principal.organization_id)
    except ProductStoreError as exc:
        raise HTTPException(status_code=503, detail="Product store unavailable") from exc
    return {"products": items}


@router.post("", status_code=201)
async def new_product(payload: ProductCreateRequest, request: Request) -> dict:
    principal = _principal(request)
    require_permission(request, "workspace:edit")
    if not payload.platforms:
        raise HTTPException(status_code=400, detail="Select at least one user-experience platform")

    await _ensure_store()
    plan = build_initial_product_plan(payload)
    summary = payload.concept.strip().split("\n", 1)[0][:500]
    try:
        return await run_in_threadpool(
            create_product,
            organization_id=principal.organization_id,
            owner_user_id=principal.user_id,
            name=payload.name.strip(),
            summary=summary,
            concept=payload.concept.strip(),
            target_users=[value.strip() for value in payload.target_users if value.strip()],
            platforms=list(dict.fromkeys(payload.platforms)),
            capabilities=list(dict.fromkeys(payload.capabilities)),
            constraints=[value.strip() for value in payload.constraints if value.strip()],
            components=plan["components"],
            deliverables=plan["deliverables"],
            dependencies=plan["dependencies"],
        )
    except ProductStoreError as exc:
        raise HTTPException(status_code=503, detail="Unable to create product") from exc


@router.get("/{product_id}")
async def product(product_id: str, request: Request) -> dict:
    principal = _principal(request)
    require_permission(request, "workspace:read")
    await _ensure_store()
    try:
        return await run_in_threadpool(
            get_product_snapshot,
            product_id=product_id,
            organization_id=principal.organization_id,
        )
    except ProductNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Product not found") from exc
    except ProductStoreError as exc:
        raise HTTPException(status_code=503, detail="Product store unavailable") from exc


@router.patch("/{product_id}/deliverables/{deliverable_id}")
async def change_deliverable_status(
    product_id: str,
    deliverable_id: str,
    payload: DeliverableStatusRequest,
    request: Request,
) -> dict:
    principal = _principal(request)
    require_permission(request, "workspace:edit")
    await _ensure_store()
    try:
        return await run_in_threadpool(
            update_deliverable_status,
            product_id=product_id,
            deliverable_id=deliverable_id,
            organization_id=principal.organization_id,
            status=payload.status,
        )
    except ProductNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Deliverable not found") from exc
    except ProductValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ProductStoreError as exc:
        raise HTTPException(status_code=503, detail="Product store unavailable") from exc
