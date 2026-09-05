from datetime import datetime, timezone
from fastapi import APIRouter
from app.core.config import get_settings

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict:
    settings = get_settings()
    return {
        "status": "ok",
        "service": settings.app_name,
        "environment": settings.app_env,
        "aquila_provider": settings.aquila_provider,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
