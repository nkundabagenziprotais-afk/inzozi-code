from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.security.identity_store import ping_identity_store
from app.security.redis_controls import ping_auth_state_store

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    """Liveness probe. Does not fail closed on Redis outage."""
    settings = get_settings()
    return {
        "status": "ok",
        "service": settings.app_name,
        "environment": settings.app_env,
        "aquila_provider": settings.aquila_provider,
        "auth_enabled": settings.auth_enabled,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/ready")
async def ready():
    """Readiness probe. Auth-enabled Redis (and identity DB in database mode) must be ready."""
    settings = get_settings()
    if not settings.auth_enabled:
        return {
            "status": "ready",
            "auth_enabled": False,
            "auth_state_store": "disabled",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    store_ok = await ping_auth_state_store()
    payload: dict = {
        "status": "ready" if store_ok else "not_ready",
        "auth_enabled": True,
        "auth_identity_mode": settings.auth_identity_mode,
        "auth_state_store": "ready" if store_ok else "unavailable",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    if settings.auth_identity_mode == "database":
        identity_ok = await run_in_threadpool(ping_identity_store)
        payload["identity_store"] = "ready" if identity_ok else "unavailable"
        if not store_ok or not identity_ok:
            payload["status"] = "not_ready"
            return JSONResponse(status_code=503, content=payload)
        return payload

    payload["identity_store"] = "disabled"
    if not store_ok:
        return JSONResponse(status_code=503, content=payload)
    return payload
