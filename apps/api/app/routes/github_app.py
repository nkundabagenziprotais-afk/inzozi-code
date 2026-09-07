from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.core.config import get_settings
from app.integrations.github_app import (
    GitHubAppError,
    default_installation_for,
    get_installation_summary,
    github_app_configured,
    verify_repository_access,
)

router = APIRouter(prefix="/v1/github-app", tags=["github-app"])


def _configured_installation_id() -> int:
    settings = get_settings()
    try:
        installation_id = int(settings.github_app_default_installation_id)
    except (TypeError, ValueError) as exc:
        raise GitHubAppError("Configured GitHub App installation ID is invalid") from exc
    if installation_id <= 0:
        raise GitHubAppError("Configured GitHub App installation ID is invalid")
    return installation_id


@router.get("/status")
async def github_app_status() -> dict:
    settings = get_settings()
    if not github_app_configured():
        return {
            "configured": False,
            "owner": settings.github_app_owner or None,
            "owner_matches_installation": False,
            "key_loaded": False,
            "installation": None,
            "gates": {
                "private_clone": False,
                "remote_push": False,
                "draft_pull_request": False,
            },
            "message": "GitHub App staging credentials are not fully configured.",
        }

    try:
        installation_id = _configured_installation_id()
        summary = await get_installation_summary(installation_id)
    except GitHubAppError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    owner_matches = bool(summary["account"]) and summary["account"].casefold() == settings.github_app_owner.casefold()
    readiness = summary["readiness"]
    gates = {
        "private_clone": owner_matches and not summary["suspended"] and readiness["private_clone"],
        "remote_push": owner_matches and not summary["suspended"] and readiness["remote_push"],
        "draft_pull_request": owner_matches and not summary["suspended"] and readiness["draft_pull_request"],
    }
    return {
        "configured": True,
        "owner": settings.github_app_owner,
        "owner_matches_installation": owner_matches,
        "key_loaded": True,
        "installation": {
            "account": summary["account"],
            "repository_selection": summary["repository_selection"],
            "permissions": summary["permissions"],
            "suspended": summary["suspended"],
        },
        "gates": gates,
        "message": "GitHub App installation is ready for the enabled gates." if all(gates.values()) else "GitHub App installation needs attention before private-repository E2E.",
    }


@router.post("/verify-repository")
async def verify_github_repository(request: Request) -> dict:
    if not github_app_configured():
        raise HTTPException(status_code=409, detail="GitHub App staging credentials are not fully configured")

    body = await request.json()
    repository_url = body.get("repository_url")
    if not isinstance(repository_url, str) or not repository_url.strip():
        raise HTTPException(status_code=400, detail="repository_url is required")
    repository_url = repository_url.strip()

    try:
        installation_id = default_installation_for(repository_url)
        if installation_id is None:
            raise GitHubAppError("Repository owner does not match the configured GitHub App installation owner")
        repository = await verify_repository_access(repository_url, installation_id)
    except GitHubAppError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {
        "verified": True,
        "repository": repository,
        "token_returned": False,
        "permission_used": "contents:read",
        "message": "Repository-scoped private clone access is verified.",
    }
