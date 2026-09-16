from __future__ import annotations

from fastapi import APIRouter

from app.core.config import get_settings
from app.integrations.github_app import github_app_configured

router = APIRouter(prefix="/v1/engineering-readiness", tags=["engineering-readiness"])


@router.get("")
def engineering_readiness() -> dict:
    settings = get_settings()
    return {
        "github_app_configured": github_app_configured(),
        "workspace_ownership_enforced": settings.workspace_ownership_enforced,
    }
