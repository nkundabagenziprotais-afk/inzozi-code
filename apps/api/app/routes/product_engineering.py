from __future__ import annotations

from functools import lru_cache
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.product.engineering_sync import (
    EngineeringSyncError,
    assign_deliverable_to_module,
    ensure_engineering_sync_schema,
    get_engineering_summary,
    record_engineering_event,
    update_engineering_binding,
)
from app.product.module_delivery import create_module_deliverable
from app.product.repository_binding import (
    RepositoryBindingError,
    bind_module_repository,
    ensure_repository_binding_schema,
    get_repository_state,
    remember_repository_workspace,
    save_product_repository,
)
from app.product.store import (
    ProductNotFoundError,
    ProductStoreError,
    ProductValidationError,
)
from app.security.auth import AuthPrincipal, require_permission

router = APIRouter(prefix="/v1/products/{product_id}/engineering", tags=["product-engineering"])

EngineeringEventType = Literal[
    "engineering_opened",
    "workspace_opened",
    "command_completed",
    "git_review_prepared",
    "commit_created",
    "push_completed",
    "pull_request_created",
    "agent_run_completed",
]
EngineeringEventStatus = Literal["info", "success", "failure"]


class EngineeringBindingRequest(BaseModel):
    module_id: str | None = None
    deliverable_id: str | None = None
    repository_url: str | None = Field(default=None, max_length=2000)
    workspace_id: str | None = Field(default=None, max_length=160)
    repository_ref: str | None = Field(default=None, max_length=255)


class DeliverableModuleRequest(BaseModel):
    module_id: str | None = None


class ModuleDeliverableCreateRequest(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    description: str = Field(default="", max_length=4000)


class ProductRepositoryRequest(BaseModel):
    repository_url: str = Field(min_length=8, max_length=2000)
    repository_ref: str | None = Field(default=None, max_length=255)
    make_default: bool = False


class ModuleRepositoryRequest(BaseModel):
    repository_id: str | None = None


class EngineeringEventRequest(BaseModel):
    event_type: EngineeringEventType
    status: EngineeringEventStatus = "info"
    summary: str = Field(min_length=2, max_length=500)
    evidence: dict[str, Any] = Field(default_factory=dict)


def _principal(request: Request) -> AuthPrincipal:
    principal = getattr(request.state, "principal", None)
    if not isinstance(principal, AuthPrincipal):
        raise HTTPException(status_code=401, detail="Authentication required")
    return principal


@lru_cache(maxsize=1)
def _ensure_sync_ready() -> None:
    ensure_engineering_sync_schema()
    ensure_repository_binding_schema()


async def _ensure_store() -> None:
    try:
        await run_in_threadpool(_ensure_sync_ready)
    except ProductStoreError as exc:
        raise HTTPException(status_code=503, detail="Engineering synchronization unavailable") from exc


def _summary_with_repository_state(*, product_id: str, organization_id: str) -> dict[str, Any]:
    summary = get_engineering_summary(product_id=product_id, organization_id=organization_id)
    binding = summary.get("binding") or {}
    repository_state = get_repository_state(
        product_id=product_id,
        organization_id=organization_id,
        module_id=binding.get("module_id"),
    )
    return {**summary, **repository_state}


def _decorate_summary(*, summary: dict[str, Any], product_id: str, organization_id: str) -> dict[str, Any]:
    binding = summary.get("binding") or {}
    repository_state = get_repository_state(
        product_id=product_id,
        organization_id=organization_id,
        module_id=binding.get("module_id"),
    )
    return {**summary, **repository_state}


@router.get("")
async def engineering_summary(product_id: str, request: Request) -> dict:
    principal = _principal(request)
    require_permission(request, "workspace:read")
    await _ensure_store()
    try:
        return await run_in_threadpool(
            _summary_with_repository_state,
            product_id=product_id,
            organization_id=principal.organization_id,
        )
    except ProductNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Product not found") from exc
    except ProductStoreError as exc:
        raise HTTPException(status_code=503, detail="Engineering synchronization unavailable") from exc


@router.put("/binding")
async def engineering_binding(
    product_id: str,
    payload: EngineeringBindingRequest,
    request: Request,
) -> dict:
    principal = _principal(request)
    require_permission(request, "workspace:edit")
    await _ensure_store()
    changes = payload.model_dump(exclude_unset=True)
    try:
        summary = await run_in_threadpool(
            update_engineering_binding,
            product_id=product_id,
            organization_id=principal.organization_id,
            changes=changes,
        )
        repository_url = changes.get("repository_url")
        workspace_id = changes.get("workspace_id")
        if repository_url and workspace_id:
            binding = summary.get("binding") or {}
            await run_in_threadpool(
                remember_repository_workspace,
                product_id=product_id,
                organization_id=principal.organization_id,
                repository_url=repository_url,
                repository_ref=changes.get("repository_ref") or binding.get("repository_ref"),
                workspace_id=workspace_id,
                module_id=binding.get("module_id"),
            )
        return await run_in_threadpool(
            _decorate_summary,
            summary=summary,
            product_id=product_id,
            organization_id=principal.organization_id,
        )
    except ProductNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Product not found") from exc
    except ProductValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ProductStoreError as exc:
        raise HTTPException(status_code=503, detail="Unable to update engineering synchronization") from exc


@router.post("/repositories", status_code=201)
async def product_repository(
    product_id: str,
    payload: ProductRepositoryRequest,
    request: Request,
) -> dict:
    principal = _principal(request)
    require_permission(request, "workspace:edit")
    await _ensure_store()
    try:
        return await run_in_threadpool(
            save_product_repository,
            product_id=product_id,
            organization_id=principal.organization_id,
            repository_url=payload.repository_url,
            repository_ref=payload.repository_ref,
            make_default=payload.make_default,
        )
    except ProductNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Product not found") from exc
    except ProductValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RepositoryBindingError as exc:
        raise HTTPException(status_code=503, detail="Unable to save engineering repository") from exc


@router.put("/modules/{module_id}/repository")
async def module_repository(
    product_id: str,
    module_id: str,
    payload: ModuleRepositoryRequest,
    request: Request,
) -> dict:
    principal = _principal(request)
    require_permission(request, "workspace:edit")
    await _ensure_store()
    try:
        return await run_in_threadpool(
            bind_module_repository,
            product_id=product_id,
            organization_id=principal.organization_id,
            module_id=module_id,
            repository_id=payload.repository_id,
        )
    except ProductNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Product not found") from exc
    except ProductValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RepositoryBindingError as exc:
        raise HTTPException(status_code=503, detail="Unable to bind module repository") from exc


@router.post("/modules/{module_id}/deliverables", status_code=201)
async def new_module_deliverable(
    product_id: str,
    module_id: str,
    payload: ModuleDeliverableCreateRequest,
    request: Request,
) -> dict:
    principal = _principal(request)
    require_permission(request, "workspace:edit")
    await _ensure_store()
    try:
        summary = await run_in_threadpool(
            create_module_deliverable,
            product_id=product_id,
            organization_id=principal.organization_id,
            module_id=module_id,
            title=payload.title,
            description=payload.description,
        )
        return await run_in_threadpool(
            _decorate_summary,
            summary=summary,
            product_id=product_id,
            organization_id=principal.organization_id,
        )
    except ProductNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Product or module not found") from exc
    except ProductValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ProductStoreError as exc:
        raise HTTPException(status_code=503, detail="Unable to create module deliverable") from exc


@router.put("/deliverables/{deliverable_id}/module")
async def deliverable_module_binding(
    product_id: str,
    deliverable_id: str,
    payload: DeliverableModuleRequest,
    request: Request,
) -> dict:
    principal = _principal(request)
    require_permission(request, "workspace:edit")
    await _ensure_store()
    try:
        summary = await run_in_threadpool(
            assign_deliverable_to_module,
            product_id=product_id,
            organization_id=principal.organization_id,
            deliverable_id=deliverable_id,
            module_id=payload.module_id,
        )
        return await run_in_threadpool(
            _decorate_summary,
            summary=summary,
            product_id=product_id,
            organization_id=principal.organization_id,
        )
    except ProductNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Product not found") from exc
    except ProductValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ProductStoreError as exc:
        raise HTTPException(status_code=503, detail="Unable to bind deliverable to module") from exc


@router.post("/events", status_code=201)
async def engineering_event(
    product_id: str,
    payload: EngineeringEventRequest,
    request: Request,
) -> dict:
    principal = _principal(request)
    require_permission(request, "workspace:edit")
    await _ensure_store()
    try:
        summary = await run_in_threadpool(
            record_engineering_event,
            product_id=product_id,
            organization_id=principal.organization_id,
            event_type=payload.event_type,
            status=payload.status,
            summary=payload.summary,
            evidence=payload.evidence,
        )
        return await run_in_threadpool(
            _decorate_summary,
            summary=summary,
            product_id=product_id,
            organization_id=principal.organization_id,
        )
    except ProductNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Product not found") from exc
    except ProductValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ProductStoreError as exc:
        raise HTTPException(status_code=503, detail="Unable to record engineering evidence") from exc
