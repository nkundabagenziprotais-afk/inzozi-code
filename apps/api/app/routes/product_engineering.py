from __future__ import annotations

from functools import lru_cache
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.product.engineering_sync import (
    EngineeringSyncError,
    ensure_engineering_sync_schema,
    get_engineering_summary,
    record_engineering_event,
    update_engineering_binding,
)
from app.product.store import ProductNotFoundError, ProductValidationError
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


async def _ensure_store() -> None:
    try:
        await run_in_threadpool(_ensure_sync_ready)
    except EngineeringSyncError as exc:
        raise HTTPException(status_code=503, detail="Engineering synchronization unavailable") from exc


@router.get("")
async def engineering_summary(product_id: str, request: Request) -> dict:
    principal = _principal(request)
    require_permission(request, "workspace:read")
    await _ensure_store()
    try:
        return await run_in_threadpool(
            get_engineering_summary,
            product_id=product_id,
            organization_id=principal.organization_id,
        )
    except ProductNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Product not found") from exc
    except EngineeringSyncError as exc:
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
        return await run_in_threadpool(
            update_engineering_binding,
            product_id=product_id,
            organization_id=principal.organization_id,
            changes=changes,
        )
    except ProductNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Product not found") from exc
    except ProductValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except EngineeringSyncError as exc:
        raise HTTPException(status_code=503, detail="Unable to update engineering synchronization") from exc


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
        return await run_in_threadpool(
            record_engineering_event,
            product_id=product_id,
            organization_id=principal.organization_id,
            event_type=payload.event_type,
            status=payload.status,
            summary=payload.summary,
            evidence=payload.evidence,
        )
    except ProductNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Product not found") from exc
    except ProductValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except EngineeringSyncError as exc:
        raise HTTPException(status_code=503, detail="Unable to record engineering evidence") from exc
