from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import re
import secrets
from typing import Final

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import get_settings
from app.security.redis_controls import (
    AuthStateUnavailableError,
    SessionInactiveError,
    session_claims_fingerprint,
    validate_session,
)

SESSION_COOKIE: Final[str] = "inzozi_session"
PASSWORD_SCHEME: Final[str] = "pbkdf2_sha256"
ROLE_NAMES: Final[tuple[str, ...]] = (
    "platform_owner",
    "org_admin",
    "developer",
    "reviewer",
    "deployment_approver",
    "viewer",
)

ALL_PERMISSIONS: Final[frozenset[str]] = frozenset(
    {
        "agent:use",
        "workspace:read",
        "workspace:create",
        "workspace:delete",
        "workspace:edit",
        "workspace:run",
        "git:commit",
        "git:push",
        "git:pull_request",
        "integration:read",
        "deployment:plan",
        "deployment:approve",
        "rbac:admin",
    }
)

ROLE_PERMISSIONS: Final[dict[str, frozenset[str]]] = {
    "platform_owner": ALL_PERMISSIONS,
    "org_admin": ALL_PERMISSIONS - {"deployment:approve"},
    "developer": frozenset(
        {
            "agent:use",
            "workspace:read",
            "workspace:create",
            "workspace:delete",
            "workspace:edit",
            "workspace:run",
            "git:commit",
            "git:push",
            "git:pull_request",
        }
    ),
    "reviewer": frozenset({"agent:use", "workspace:read"}),
    "deployment_approver": frozenset({"agent:use", "workspace:read", "deployment:plan", "deployment:approve"}),
    "viewer": frozenset({"agent:use", "workspace:read"}),
}

PUBLIC_PATHS: Final[frozenset[str]] = frozenset({"/", "/health", "/ready", "/v1/auth/login"})
WORKSPACE_ID_RE: Final[re.Pattern[str]] = re.compile(r"^/v1/workspaces/[0-9a-f]{32}(?:/|$)")


class AuthenticationError(RuntimeError):
    pass


@dataclass(frozen=True)
class AuthPrincipal:
    email: str
    role: str
    organization_id: str
    permissions: frozenset[str]
    session_id: str
    expires_at: datetime | None
    auth_enabled: bool = True

    def has(self, permission: str) -> bool:
        return permission in self.permissions

    def public_payload(self) -> dict:
        return {
            "email": self.email,
            "role": self.role,
            "organization_id": self.organization_id,
            "permissions": sorted(self.permissions),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "auth_enabled": self.auth_enabled,
        }


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(value + padding)
    except Exception as exc:
        raise AuthenticationError("Invalid encoded authentication payload") from exc


def hash_password(password: str, *, iterations: int = 600_000, salt: bytes | None = None) -> str:
    if len(password) < 12:
        raise ValueError("Staging access passwords must contain at least 12 characters")
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations, dklen=32)
    return f"{PASSWORD_SCHEME}${iterations}${_b64url_encode(salt)}${_b64url_encode(digest)}"


def verify_password(password: str, encoded_hash: str) -> bool:
    try:
        scheme, iterations_text, salt_text, expected_text = encoded_hash.split("$", 3)
        if scheme != PASSWORD_SCHEME:
            return False
        iterations = int(iterations_text)
        if not 100_000 <= iterations <= 2_000_000:
            return False
        salt = _b64url_decode(salt_text)
        expected = _b64url_decode(expected_text)
    except (ValueError, AuthenticationError):
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations, dklen=len(expected))
    return hmac.compare_digest(actual, expected)


def _session_secret() -> bytes:
    settings = get_settings()
    secret = settings.auth_session_secret
    if len(secret) < 32:
        raise AuthenticationError("AUTH_SESSION_SECRET must contain at least 32 characters")
    return secret.encode("utf-8")


def create_session_token(email: str, role: str, organization_id: str) -> tuple[str, datetime]:
    settings = get_settings()
    if role not in ROLE_PERMISSIONS:
        raise AuthenticationError("Configured authentication role is invalid")
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=settings.auth_session_ttl_minutes)
    payload = {
        "sub": email.casefold(),
        "role": role,
        "org": organization_id,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
        "jti": secrets.token_urlsafe(18),
    }
    encoded_payload = _b64url_encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signature = hmac.new(_session_secret(), encoded_payload.encode("ascii"), hashlib.sha256).digest()
    return f"{encoded_payload}.{_b64url_encode(signature)}", expires_at


def decode_session_token(token: str) -> AuthPrincipal:
    try:
        encoded_payload, encoded_signature = token.split(".", 1)
    except ValueError as exc:
        raise AuthenticationError("Invalid session token") from exc
    expected_signature = hmac.new(_session_secret(), encoded_payload.encode("ascii"), hashlib.sha256).digest()
    supplied_signature = _b64url_decode(encoded_signature)
    if not hmac.compare_digest(expected_signature, supplied_signature):
        raise AuthenticationError("Invalid session signature")
    try:
        payload = json.loads(_b64url_decode(encoded_payload))
        email = str(payload["sub"])
        role = str(payload["role"])
        organization_id = str(payload["org"])
        session_id = str(payload["jti"])
        expires_timestamp = int(payload["exp"])
        issued_timestamp = int(payload["iat"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise AuthenticationError("Invalid session payload") from exc
    if role not in ROLE_PERMISSIONS:
        raise AuthenticationError("Unknown session role")
    now_timestamp = int(datetime.now(timezone.utc).timestamp())
    if expires_timestamp <= now_timestamp:
        raise AuthenticationError("Session expired")
    if issued_timestamp > now_timestamp + 60:
        raise AuthenticationError("Session issue time is invalid")
    return AuthPrincipal(
        email=email,
        role=role,
        organization_id=organization_id,
        permissions=ROLE_PERMISSIONS[role],
        session_id=session_id,
        expires_at=datetime.fromtimestamp(expires_timestamp, tz=timezone.utc),
    )


def development_principal() -> AuthPrincipal:
    settings = get_settings()
    return AuthPrincipal(
        email="development@local",
        role="platform_owner",
        organization_id=settings.auth_organization_id,
        permissions=ROLE_PERMISSIONS["platform_owner"],
        session_id="auth-disabled",
        expires_at=None,
        auth_enabled=False,
    )


def permission_for_request(method: str, path: str) -> str | None:
    if not path.startswith("/v1/"):
        return None
    if path.startswith("/v1/auth/"):
        return None
    if path.startswith("/v1/github-app"):
        return "integration:read"
    if path.startswith("/v1/agent"):
        return "agent:use"
    if not path.startswith("/v1/workspaces"):
        return None
    if method == "GET":
        return "workspace:read"
    if method == "DELETE" and WORKSPACE_ID_RE.match(path):
        return "workspace:delete"
    if method == "PUT" and "/files/" in path:
        return "workspace:edit"
    if method == "POST":
        if path == "/v1/workspaces":
            return "workspace:create"
        if path.endswith("/actions"):
            return "workspace:run"
        if "/git/branches" in path or "/git/commit/" in path:
            return "git:commit"
        if "/git/push/" in path:
            return "git:push"
        if "/git/pull-request/" in path:
            return "git:pull_request"
    return "workspace:read"


def principal_from_request(request: Request) -> AuthPrincipal:
    principal = getattr(request.state, "principal", None)
    if not isinstance(principal, AuthPrincipal):
        raise HTTPException(status_code=401, detail="Authentication required")
    return principal


def require_permission(request: Request, permission: str) -> AuthPrincipal:
    principal = principal_from_request(request)
    if not principal.has(permission):
        raise HTTPException(status_code=403, detail="Your role does not allow this action")
    return principal


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        settings = get_settings()
        path = request.url.path
        if request.method == "OPTIONS" or path in PUBLIC_PATHS:
            return await call_next(request)

        if not settings.auth_enabled:
            request.state.principal = development_principal()
            return await call_next(request)

        token = request.cookies.get(SESSION_COOKIE)
        if not token:
            return JSONResponse(status_code=401, content={"detail": "Authentication required"})
        try:
            principal = decode_session_token(token)
        except AuthenticationError:
            response = JSONResponse(status_code=401, content={"detail": "Session expired or invalid"})
            response.delete_cookie(SESSION_COOKIE, path="/")
            return response

        fingerprint = session_claims_fingerprint(
            email=principal.email,
            role=principal.role,
            organization_id=principal.organization_id,
        )
        try:
            await validate_session(jti=principal.session_id, fingerprint=fingerprint)
        except AuthStateUnavailableError:
            return JSONResponse(
                status_code=503,
                content={"detail": "Authentication state service unavailable"},
            )
        except SessionInactiveError:
            response = JSONResponse(status_code=401, content={"detail": "Session expired or invalid"})
            response.delete_cookie(SESSION_COOKIE, path="/")
            return response

        request.state.principal = principal

        permission = permission_for_request(request.method, path)
        if permission and not principal.has(permission):
            return JSONResponse(status_code=403, content={"detail": "Your role does not allow this action"})
        return await call_next(request)
