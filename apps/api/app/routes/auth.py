from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.security.auth import (
    SESSION_COOKIE,
    ROLE_PERMISSIONS,
    create_session_token,
    decode_session_token,
    development_principal,
    principal_from_request,
    verify_password,
)
from app.security.redis_controls import (
    MAX_FAILED_LOGINS,
    AuthStateUnavailableError,
    complete_successful_login,
    get_login_failure_count,
    login_identifier_digest,
    record_login_failure,
    remaining_session_ttl_seconds,
    resolve_login_client_host,
    revoke_session,
    session_claims_fingerprint,
)

router = APIRouter(prefix="/v1/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=1024)


def _login_digest(request: Request, email: str) -> str:
    peer = request.client.host if request.client else "unknown"
    client_host = resolve_login_client_host(
        peer_host=peer,
        x_real_ip=request.headers.get("x-real-ip"),
        x_forwarded_for=request.headers.get("x-forwarded-for"),
    )
    return login_identifier_digest(client_host=client_host, email=email)


@router.post("/login")
async def login(payload: LoginRequest, request: Request, response: Response) -> dict:
    settings = get_settings()
    if not settings.auth_enabled:
        return development_principal().public_payload()

    if not settings.auth_bootstrap_email or not settings.auth_password_hash or not settings.auth_session_secret:
        raise HTTPException(status_code=503, detail="Staging authentication is not fully configured")

    email = payload.email.casefold().strip()
    digest = _login_digest(request, email)

    try:
        if await get_login_failure_count(digest) >= MAX_FAILED_LOGINS:
            raise HTTPException(status_code=429, detail="Too many sign-in attempts. Try again in a few minutes.")
    except AuthStateUnavailableError as exc:
        raise HTTPException(status_code=503, detail="Authentication state service unavailable") from exc

    email_matches = email == settings.auth_bootstrap_email.casefold().strip()
    # PBKDF2 is CPU-bound; keep it off the async event loop while still always verifying
    # so unknown emails and wrong passwords take the same password-check path.
    password_matches = await run_in_threadpool(verify_password, payload.password, settings.auth_password_hash)
    if not (email_matches and password_matches):
        try:
            failure_count = await record_login_failure(digest)
        except AuthStateUnavailableError as exc:
            raise HTTPException(status_code=503, detail="Authentication state service unavailable") from exc
        if failure_count > MAX_FAILED_LOGINS:
            raise HTTPException(status_code=429, detail="Too many sign-in attempts. Try again in a few minutes.")
        raise HTTPException(status_code=401, detail="Email or password is incorrect")

    role = settings.auth_bootstrap_role
    if role not in ROLE_PERMISSIONS:
        raise HTTPException(status_code=503, detail="Configured bootstrap role is invalid")

    token, expires_at = create_session_token(email, role, settings.auth_organization_id)
    principal = decode_session_token(token)
    fingerprint = session_claims_fingerprint(
        email=principal.email,
        role=principal.role,
        organization_id=principal.organization_id,
    )
    try:
        await complete_successful_login(
            identifier_digest=digest,
            jti=principal.session_id,
            fingerprint=fingerprint,
            ttl_seconds=remaining_session_ttl_seconds(expires_at),
        )
    except AuthStateUnavailableError as exc:
        raise HTTPException(status_code=503, detail="Authentication state service unavailable") from exc

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
async def logout(request: Request, response: Response) -> None:
    settings = get_settings()
    if settings.auth_enabled:
        principal = getattr(request.state, "principal", None)
        if principal is not None and getattr(principal, "session_id", None):
            try:
                await revoke_session(jti=principal.session_id)
            except AuthStateUnavailableError as exc:
                raise HTTPException(status_code=503, detail="Authentication state service unavailable") from exc
    response.delete_cookie(SESSION_COOKIE, path="/")
