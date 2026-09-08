from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.security.auth import (
    BOOTSTRAP_USER_ID,
    DUMMY_PASSWORD_HASH,
    SESSION_COOKIE,
    ROLE_PERMISSIONS,
    create_session_token,
    decode_session_token,
    development_principal,
    hash_password,
    principal_from_request,
    require_permission,
    verify_password,
)
from app.security.identity_store import (
    IdentityAuthorizationError,
    IdentityConflictError,
    IdentityStoreError,
    IdentityValidationError,
    activate_invitation,
    count_active_platform_owners,
    create_invitation,
    get_user_by_email,
    get_user_by_id,
    list_users,
    revoke_all_sessions,
    update_user,
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

GENERIC_CREDENTIAL_FAILURE = "Email or password is incorrect"
GENERIC_INVITATION_FAILURE = "Invitation is invalid or expired"


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=1024)


class ActivateRequest(BaseModel):
    token: str = Field(min_length=8, max_length=512)
    password: str = Field(min_length=12, max_length=1024)


class InvitationRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    organization_id: str | None = Field(default=None, min_length=1, max_length=100)
    role: str | None = Field(default=None, min_length=1, max_length=50)


class PatchUserRequest(BaseModel):
    role: str | None = Field(default=None, min_length=1, max_length=50)
    organization_id: str | None = Field(default=None, min_length=1, max_length=100)
    status: str | None = Field(default=None, min_length=1, max_length=20)


async def _login_digest(request: Request, email: str) -> str:
    peer = request.client.host if request.client else "unknown"
    # Extract header strings on the event loop; resolve (may DNS) off-loop.
    x_real_ip = request.headers.get("x-real-ip")
    x_forwarded_for = request.headers.get("x-forwarded-for")
    client_host = await run_in_threadpool(
        resolve_login_client_host,
        peer_host=peer,
        x_real_ip=x_real_ip,
        x_forwarded_for=x_forwarded_for,
    )
    return login_identifier_digest(client_host=client_host, email=email)


async def _issue_session_cookie(
    *,
    response: Response,
    email: str,
    role: str,
    organization_id: str,
    user_id: str,
    session_version: int,
    digest: str,
) -> dict:
    settings = get_settings()
    token, expires_at = create_session_token(
        email,
        role,
        organization_id,
        user_id=user_id,
        session_version=session_version,
    )
    principal = decode_session_token(token)
    fingerprint = session_claims_fingerprint(
        email=principal.email,
        role=principal.role,
        organization_id=principal.organization_id,
        user_id=principal.user_id,
        session_version=principal.session_version,
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
        "organization_id": organization_id,
        "user_id": user_id,
        "session_version": session_version,
        "permissions": sorted(ROLE_PERMISSIONS[role]),
        "expires_at": expires_at.isoformat(),
        "auth_enabled": True,
    }


async def _record_failed_login(digest: str) -> None:
    try:
        failure_count = await record_login_failure(digest)
    except AuthStateUnavailableError as exc:
        raise HTTPException(status_code=503, detail="Authentication state service unavailable") from exc
    if failure_count > MAX_FAILED_LOGINS:
        raise HTTPException(status_code=429, detail="Too many sign-in attempts. Try again in a few minutes.")
    raise HTTPException(status_code=401, detail=GENERIC_CREDENTIAL_FAILURE)


@router.post("/login")
async def login(payload: LoginRequest, request: Request, response: Response) -> dict:
    settings = get_settings()
    if not settings.auth_enabled:
        return development_principal().public_payload()

    if not settings.auth_session_secret or len(settings.auth_session_secret) < 32:
        raise HTTPException(status_code=503, detail="Staging authentication is not fully configured")

    email = payload.email.casefold().strip()
    digest = await _login_digest(request, email)

    try:
        if await get_login_failure_count(digest) >= MAX_FAILED_LOGINS:
            raise HTTPException(status_code=429, detail="Too many sign-in attempts. Try again in a few minutes.")
    except AuthStateUnavailableError as exc:
        raise HTTPException(status_code=503, detail="Authentication state service unavailable") from exc

    if settings.auth_identity_mode == "database":
        return await _login_database(payload=payload, email=email, digest=digest, response=response)

    return await _login_bootstrap(payload=payload, email=email, digest=digest, response=response)


async def _login_bootstrap(*, payload: LoginRequest, email: str, digest: str, response: Response) -> dict:
    settings = get_settings()
    if not settings.auth_bootstrap_email or not settings.auth_password_hash:
        raise HTTPException(status_code=503, detail="Staging authentication is not fully configured")

    email_matches = email == settings.auth_bootstrap_email.casefold().strip()
    # PBKDF2 is CPU-bound; keep it off the async event loop while still always verifying
    # so unknown emails and wrong passwords take the same password-check path.
    password_matches = await run_in_threadpool(verify_password, payload.password, settings.auth_password_hash)
    if not (email_matches and password_matches):
        await _record_failed_login(digest)

    role = settings.auth_bootstrap_role
    if role not in ROLE_PERMISSIONS:
        raise HTTPException(status_code=503, detail="Configured bootstrap role is invalid")

    return await _issue_session_cookie(
        response=response,
        email=email,
        role=role,
        organization_id=settings.auth_organization_id,
        user_id=BOOTSTRAP_USER_ID,
        session_version=0,
        digest=digest,
    )


async def _login_database(*, payload: LoginRequest, email: str, digest: str, response: Response) -> dict:
    # Database mode never authenticates against bootstrap env credentials.
    try:
        user = await run_in_threadpool(lambda: get_user_by_email(email, include_hash=True))
    except IdentityStoreError as exc:
        raise HTTPException(status_code=503, detail="Identity store unavailable") from exc

    if user is None or user.status != "active" or not user.password_hash:
        await run_in_threadpool(verify_password, payload.password, DUMMY_PASSWORD_HASH)
        await _record_failed_login(digest)

    password_matches = await run_in_threadpool(verify_password, payload.password, user.password_hash)
    if not password_matches:
        await _record_failed_login(digest)

    if user.role not in ROLE_PERMISSIONS:
        raise HTTPException(status_code=503, detail="Configured authentication role is invalid")

    return await _issue_session_cookie(
        response=response,
        email=user.email,
        role=user.role,
        organization_id=user.organization_id,
        user_id=user.user_id,
        session_version=user.session_version,
        digest=digest,
    )


@router.post("/activate")
async def activate(payload: ActivateRequest) -> dict:
    settings = get_settings()
    if not settings.auth_enabled:
        raise HTTPException(status_code=503, detail="Authentication is not enabled")
    if settings.auth_identity_mode != "database":
        raise HTTPException(status_code=503, detail="Invitation activation requires database identity mode")

    try:
        password_hash = await run_in_threadpool(hash_password, payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        user = await run_in_threadpool(
            lambda: activate_invitation(token=payload.token.strip(), password_hash=password_hash)
        )
    except IdentityValidationError as exc:
        raise HTTPException(status_code=400, detail=GENERIC_INVITATION_FAILURE) from exc
    except IdentityStoreError as exc:
        raise HTTPException(status_code=503, detail="Identity store unavailable") from exc

    return {"status": "activated", "user": user.public_dict()}


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


def _require_admin(request: Request):
    return require_permission(request, "rbac:admin")


def _assert_org_admin_may_manage(*, actor, target_org: str, target_role: str) -> None:
    if actor.role == "platform_owner":
        return
    if actor.role != "org_admin":
        raise HTTPException(status_code=403, detail="Your role does not allow this action")
    if target_org != actor.organization_id:
        raise HTTPException(status_code=403, detail="Your role does not allow this action")
    if target_role == "platform_owner":
        raise HTTPException(status_code=403, detail="Your role does not allow this action")


async def _guard_final_platform_owner(*, target_user_id: str, new_role: str, new_status: str) -> None:
    existing = await run_in_threadpool(get_user_by_id, target_user_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="User not found")
    demoting_or_disabling = existing.role == "platform_owner" and existing.status == "active" and (
        new_role != "platform_owner" or new_status != "active"
    )
    if not demoting_or_disabling:
        return
    remaining = await run_in_threadpool(lambda: count_active_platform_owners(exclude_user_id=target_user_id))
    if remaining < 1:
        raise HTTPException(status_code=409, detail="Cannot disable or demote the final active platform owner")


@router.get("/users")
async def admin_list_users(request: Request) -> dict:
    actor = _require_admin(request)
    try:
        if actor.role == "platform_owner":
            users = await run_in_threadpool(list_users)
        else:
            users = await run_in_threadpool(lambda: list_users(organization_id=actor.organization_id))
    except IdentityStoreError as exc:
        raise HTTPException(status_code=503, detail="Identity store unavailable") from exc
    return {"users": [user.public_dict() for user in users]}


@router.post("/users/invitations")
async def admin_create_invitation(payload: InvitationRequest, request: Request) -> dict:
    actor = _require_admin(request)
    role = (payload.role or "developer").strip()
    if role not in ROLE_PERMISSIONS:
        raise HTTPException(status_code=400, detail="Invalid role")
    organization_id = (payload.organization_id or actor.organization_id).strip()
    _assert_org_admin_may_manage(actor=actor, target_org=organization_id, target_role=role)

    try:
        user, token = await run_in_threadpool(
            lambda: create_invitation(
                email=payload.email,
                organization_id=organization_id,
                role=role,
                created_by_user_id=actor.user_id,
            )
        )
    except IdentityConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except IdentityAuthorizationError as exc:
        raise HTTPException(status_code=403, detail="Your role does not allow this action") from exc
    except IdentityValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except IdentityStoreError as exc:
        raise HTTPException(status_code=503, detail="Identity store unavailable") from exc

    body = user.public_dict()
    # One-time plaintext invitation token is returned only at creation.
    return {"user": body, "invitation_token": token}


@router.patch("/users/{user_id}")
async def admin_patch_user(user_id: str, payload: PatchUserRequest, request: Request) -> dict:
    actor = _require_admin(request)
    try:
        existing = await run_in_threadpool(get_user_by_id, user_id)
    except IdentityStoreError as exc:
        raise HTTPException(status_code=503, detail="Identity store unavailable") from exc
    if existing is None:
        raise HTTPException(status_code=404, detail="User not found")

    new_role = payload.role if payload.role is not None else existing.role
    new_org = payload.organization_id if payload.organization_id is not None else existing.organization_id
    new_status = payload.status if payload.status is not None else existing.status
    if new_role not in ROLE_PERMISSIONS:
        raise HTTPException(status_code=400, detail="Invalid role")
    if new_status not in {"pending", "active", "disabled"}:
        raise HTTPException(status_code=400, detail="Invalid user status")

    _assert_org_admin_may_manage(actor=actor, target_org=existing.organization_id, target_role=existing.role)
    _assert_org_admin_may_manage(actor=actor, target_org=new_org, target_role=new_role)
    if actor.role == "org_admin" and existing.role == "platform_owner":
        raise HTTPException(status_code=403, detail="Your role does not allow this action")

    await _guard_final_platform_owner(
        target_user_id=user_id,
        new_role=new_role,
        new_status=new_status,
    )

    try:
        updated = await run_in_threadpool(
            lambda: update_user(
                user_id=user_id,
                role=payload.role,
                organization_id=payload.organization_id,
                status=payload.status,
            )
        )
    except IdentityConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except IdentityValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except IdentityStoreError as exc:
        raise HTTPException(status_code=503, detail="Identity store unavailable") from exc
    return {"user": updated.public_dict()}


@router.post("/users/{user_id}/revoke-sessions")
async def admin_revoke_sessions(user_id: str, request: Request) -> dict:
    actor = _require_admin(request)
    try:
        existing = await run_in_threadpool(get_user_by_id, user_id)
    except IdentityStoreError as exc:
        raise HTTPException(status_code=503, detail="Identity store unavailable") from exc
    if existing is None:
        raise HTTPException(status_code=404, detail="User not found")

    _assert_org_admin_may_manage(actor=actor, target_org=existing.organization_id, target_role=existing.role)
    if actor.role == "org_admin" and existing.role == "platform_owner":
        raise HTTPException(status_code=403, detail="Your role does not allow this action")

    try:
        updated = await run_in_threadpool(revoke_all_sessions, user_id)
    except IdentityValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except IdentityStoreError as exc:
        raise HTTPException(status_code=503, detail="Identity store unavailable") from exc
    return {"user": updated.public_dict()}
