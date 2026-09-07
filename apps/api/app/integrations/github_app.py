from __future__ import annotations

from pathlib import Path
from typing import Literal
from urllib.parse import urlparse
import time

import httpx
import jwt

from app.core.config import get_settings

GITHUB_API = "https://api.github.com"
GITHUB_API_VERSION = "2026-03-10"


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
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            raise GitHubAppError("Configured GitHub App private key file is not readable") from exc
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


def _app_api_headers(jwt_token: str) -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {jwt_token}",
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
        "User-Agent": "Inzozi-Code",
    }


def _repo_api_headers(token: str) -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
        "User-Agent": "Inzozi-Code",
    }


def permission_readiness(permissions: dict[str, object] | None) -> dict[str, bool]:
    permissions = permissions if isinstance(permissions, dict) else {}
    contents = permissions.get("contents")
    pull_requests = permissions.get("pull_requests")
    contents_read = contents in {"read", "write"}
    return {
        "private_clone": contents_read,
        "remote_push": contents == "write",
        "draft_pull_request": contents_read and pull_requests == "write",
    }


async def get_installation_summary(installation_id: int) -> dict:
    """Read safe installation metadata using an App JWT. No installation token is returned."""
    if not isinstance(installation_id, int) or installation_id <= 0:
        raise GitHubAppError("GitHub App installation ID is invalid")
    app_jwt = create_app_jwt()
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{GITHUB_API}/app/installations/{installation_id}",
            headers=_app_api_headers(app_jwt),
        )
    if response.status_code == 404:
        raise GitHubAppError("Configured GitHub App installation was not found")
    if response.is_error:
        raise GitHubAppError(f"Unable to inspect the GitHub App installation ({response.status_code})")
    payload = response.json()
    account = payload.get("account") if isinstance(payload, dict) else None
    account_login = account.get("login") if isinstance(account, dict) else None
    permissions = payload.get("permissions") if isinstance(payload, dict) else None
    if not isinstance(permissions, dict):
        permissions = {}
    return {
        "installation_id": installation_id,
        "account": account_login if isinstance(account_login, str) else "",
        "repository_selection": payload.get("repository_selection", "unknown") if isinstance(payload, dict) else "unknown",
        "permissions": {
            "contents": permissions.get("contents", "none"),
            "pull_requests": permissions.get("pull_requests", "none"),
        },
        "suspended": bool(payload.get("suspended_at")) if isinstance(payload, dict) else False,
        "readiness": permission_readiness(permissions),
    }


async def create_installation_token(
    installation_id: int,
    repository_url: str,
    *,
    contents_permission: Literal["read", "write"] = "read",
    pull_requests_permission: Literal["read", "write"] | None = None,
) -> str:
    """Mint a short-lived repository-scoped token with only the requested repository permissions."""
    repository_name = repository_name_from_url(repository_url)
    app_jwt = create_app_jwt()
    permissions: dict[str, str] = {"contents": contents_permission}
    if pull_requests_permission is not None:
        permissions["pull_requests"] = pull_requests_permission
    payload = {
        "repositories": [repository_name],
        "permissions": permissions,
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{GITHUB_API}/app/installations/{installation_id}/access_tokens",
            headers=_app_api_headers(app_jwt),
            json=payload,
        )
    if response.is_error:
        raise GitHubAppError(f"GitHub installation token request failed ({response.status_code})")
    token = response.json().get("token")
    if not isinstance(token, str) or not token:
        raise GitHubAppError("GitHub did not return an installation token")
    return token


async def verify_repository_access(repository_url: str, installation_id: int) -> dict:
    """Verify read access with a repository-scoped token and return only non-secret repository metadata."""
    owner, repository = repository_coordinates_from_url(repository_url)
    token = await create_installation_token(
        installation_id,
        repository_url,
        contents_permission="read",
    )
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{GITHUB_API}/repos/{owner}/{repository}",
            headers=_repo_api_headers(token),
        )
    if response.status_code == 404:
        raise GitHubAppError("GitHub App installation cannot access this repository")
    if response.is_error:
        raise GitHubAppError(f"Unable to verify repository access ({response.status_code})")
    payload = response.json()
    full_name = payload.get("full_name") if isinstance(payload, dict) else None
    default_branch = payload.get("default_branch") if isinstance(payload, dict) else None
    return {
        "full_name": full_name if isinstance(full_name, str) else f"{owner}/{repository}",
        "private": bool(payload.get("private")) if isinstance(payload, dict) else False,
        "default_branch": default_branch if isinstance(default_branch, str) else "",
        "read_access_verified": True,
    }


async def get_remote_branch_head(repository_url: str, branch: str, token: str) -> str:
    owner, repository = repository_coordinates_from_url(repository_url)
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{GITHUB_API}/repos/{owner}/{repository}/git/ref/heads/{branch}",
            headers=_repo_api_headers(token),
        )
    if response.status_code == 404:
        raise GitHubAppError("The reviewed branch is not present on GitHub. Push it before creating a pull request.")
    if response.is_error:
        raise GitHubAppError(f"Unable to verify the GitHub branch ({response.status_code})")
    sha = response.json().get("object", {}).get("sha")
    if not isinstance(sha, str) or not sha:
        raise GitHubAppError("GitHub did not return the remote branch commit")
    return sha


async def create_or_get_draft_pull_request(
    repository_url: str,
    *,
    branch: str,
    base_branch: str,
    title: str,
    body: str,
    token: str,
) -> dict:
    owner, repository = repository_coordinates_from_url(repository_url)
    headers = _repo_api_headers(token)
    params = {"state": "open", "head": f"{owner}:{branch}", "base": base_branch, "per_page": 10}
    async with httpx.AsyncClient(timeout=30.0) as client:
        existing = await client.get(
            f"{GITHUB_API}/repos/{owner}/{repository}/pulls",
            headers=headers,
            params=params,
        )
        if existing.is_error:
            raise GitHubAppError(f"Unable to inspect existing pull requests ({existing.status_code})")
        for item in existing.json():
            if item.get("head", {}).get("ref") == branch and item.get("base", {}).get("ref") == base_branch:
                return {
                    "status": "existing",
                    "number": item.get("number"),
                    "html_url": item.get("html_url"),
                    "draft": bool(item.get("draft")),
                }

        response = await client.post(
            f"{GITHUB_API}/repos/{owner}/{repository}/pulls",
            headers=headers,
            json={
                "title": title,
                "head": branch,
                "base": base_branch,
                "body": body,
                "draft": True,
                "maintainer_can_modify": False,
            },
        )
    if response.is_error:
        raise GitHubAppError(f"GitHub pull request creation failed ({response.status_code})")
    payload = response.json()
    return {
        "status": "created",
        "number": payload.get("number"),
        "html_url": payload.get("html_url"),
        "draft": bool(payload.get("draft", True)),
    }
