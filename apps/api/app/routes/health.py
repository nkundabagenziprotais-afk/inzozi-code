from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.config import get_settings
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
    """Readiness probe. Auth-enabled Redis outages are reported as not-ready."""
    settings = get_settings()
    if not settings.auth_enabled:
        return {
            "status": "ready",
            "auth_enabled": False,
            "auth_state_store": "disabled",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    store_ok = await ping_auth_state_store()
    payload = {
        "status": "ready" if store_ok else "not_ready",
        "auth_enabled": True,
        "auth_state_store": "ready" if store_ok else "unavailable",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if not store_ok:
        return JSONResponse(status_code=503, content=payload)
    return payload
