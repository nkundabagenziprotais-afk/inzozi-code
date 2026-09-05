from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse
import time

import httpx
import jwt

from app.core.config import get_settings

GITHUB_API = "https://api.github.com"


class GitHubAppError(RuntimeError):
    pass


def repository_coordinates_from_url(repository_url: str) -> tuple[str, str]:
    parsed = urlparse(repository_url)
    if parsed.scheme != "https" or parsed.netloc != "github.com":
        raise GitHubAppError("Only GitHub HTTPS repository URLs are supported")
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) != 2:
        raise GitHubAppError("Repository URL must be in https://github.com/owner/repo form")
    owner, name = parts
    if name.endswith(".git"):
        name = name[:-4]
    return owner, name


def repository_name_from_url(repository_url: str) -> str:
    return repository_coordinates_from_url(repository_url)[1]


def _private_key() -> str:
    settings = get_settings()
    if settings.github_app_private_key:
        return settings.github_app_private_key.replace("\\n", "\n")
    if settings.github_app_private_key_path:
        path = Path(settings.github_app_private_key_path)
        if not path.is_file():
            raise GitHubAppError("Configured GitHub App private key file was not found")
        return path.read_text(encoding="utf-8")
    raise GitHubAppError("GitHub App private key is not configured")


def github_app_configured() -> bool:
    settings = get_settings()
    return bool(
        settings.github_app_id
        and settings.github_app_owner
        and settings.github_app_default_installation_id
        and (settings.github_app_private_key or settings.github_app_private_key_path)
    )


def default_installation_for(repository_url: str) -> int | None:
    settings = get_settings()
    if not github_app_configured():
        return None
    owner, _ = repository_coordinates_from_url(repository_url)
    if owner.casefold() != settings.github_app_owner.casefold():
        return None
    try:
        installation_id = int(settings.github_app_default_installation_id)
    except ValueError as exc:
        raise GitHubAppError("Configured GitHub App installation ID is invalid") from exc
    if installation_id <= 0:
        raise GitHubAppError("Configured GitHub App installation ID is invalid")
    return installation_id


def create_app_jwt() -> str:
    settings = get_settings()
    if not settings.github_app_id:
        raise GitHubAppError("GitHub App ID is not configured")
    now = int(time.time())
    payload = {
        "iat": now - 30,
        "exp": now + 540,
        "iss": settings.github_app_id,
    }
    return jwt.encode(payload, _private_key(), algorithm="RS256")


async def create_installation_token(installation_id: int, repository_url: str) -> str:
    repository_name = repository_name_from_url(repository_url)
    app_jwt = create_app_jwt()
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {app_jwt}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "Inzozi-Code",
    }
    payload = {
        "repositories": [repository_name],
        "permissions": {"contents": "read"},
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{GITHUB_API}/app/installations/{installation_id}/access_tokens",
            headers=headers,
            json=payload,
        )
    if response.is_error:
        raise GitHubAppError(f"GitHub installation token request failed ({response.status_code})")
    token = response.json().get("token")
    if not isinstance(token, str) or not token:
        raise GitHubAppError("GitHub did not return an installation token")
    return token
