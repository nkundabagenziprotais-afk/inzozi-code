from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.security.auth import (
    SESSION_COOKIE,
    ROLE_PERMISSIONS,
    create_session_token,
    development_principal,
    principal_from_request,
    verify_password,
)

router = APIRouter(prefix="/v1/auth", tags=["auth"])

_FAILED_LOGINS: dict[tuple[str, str], list[float]] = {}
_LOGIN_WINDOW_SECONDS = 300
_MAX_FAILED_LOGINS = 5


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=1024)


def _login_key(request: Request, email: str) -> tuple[str, str]:
    client = request.client.host if request.client else "unknown"
    return client, email.casefold().strip()


def _prune_attempts(key: tuple[str, str]) -> list[float]:
    now = time.monotonic()
    recent = [item for item in _FAILED_LOGINS.get(key, []) if now - item < _LOGIN_WINDOW_SECONDS]
    if recent:
        _FAILED_LOGINS[key] = recent
    else:
        _FAILED_LOGINS.pop(key, None)
    return recent


def _record_failure(key: tuple[str, str]) -> None:
    recent = _prune_attempts(key)
    recent.append(time.monotonic())
    _FAILED_LOGINS[key] = recent


@router.post("/login")
def login(payload: LoginRequest, request: Request, response: Response) -> dict:
    settings = get_settings()
    if not settings.auth_enabled:
        return development_principal().public_payload()

    if not settings.auth_bootstrap_email or not settings.auth_password_hash or not settings.auth_session_secret:
        raise HTTPException(status_code=503, detail="Staging authentication is not fully configured")

    email = payload.email.casefold().strip()
    key = _login_key(request, email)
    if len(_prune_attempts(key)) >= _MAX_FAILED_LOGINS:
        raise HTTPException(status_code=429, detail="Too many sign-in attempts. Try again in a few minutes.")

    email_matches = email == settings.auth_bootstrap_email.casefold().strip()
    password_matches = verify_password(payload.password, settings.auth_password_hash)
    if not (email_matches and password_matches):
        _record_failure(key)
        raise HTTPException(status_code=401, detail="Email or password is incorrect")

    role = settings.auth_bootstrap_role
    if role not in ROLE_PERMISSIONS:
        raise HTTPException(status_code=503, detail="Configured bootstrap role is invalid")

    _FAILED_LOGINS.pop(key, None)
    token, expires_at = create_session_token(email, role, settings.auth_organization_id)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="strict",
        path="/",
        max_age=settings.auth_session_ttl_minutes * 60,
    )
    return {
        "email": email,
        "role": role,
        "organization_id": settings.auth_organization_id,
        "permissions": sorted(ROLE_PERMISSIONS[role]),
        "expires_at": expires_at.isoformat(),
        "auth_enabled": True,
    }


@router.get("/me")
def me(request: Request) -> dict:
    return principal_from_request(request).public_payload()


@router.post("/logout", status_code=204)
def logout(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")
