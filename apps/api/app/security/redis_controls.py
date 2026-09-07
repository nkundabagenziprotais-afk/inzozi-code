from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import ipaddress
import re
import socket

from redis.asyncio import Redis

from app.core.config import get_settings

LOGIN_WINDOW_SECONDS = 300
MAX_FAILED_LOGINS = 5
KEY_PREFIX = "inzozi:security:v1"
SAFE_JTI_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")

_client: Redis | object | None = None
_auth_state_ready = False


class AuthStateUnavailableError(RuntimeError):
    """Redis authentication-state store cannot be contacted or is corrupt."""


class SessionInactiveError(RuntimeError):
    """Signed session is missing, expired, revoked, or claim-mismatched in Redis."""


def login_identifier_digest(*, client_host: str, email: str) -> str:
    material = f"{client_host.strip()}\0{email.casefold().strip()}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def session_claims_fingerprint(*, email: str, role: str, organization_id: str) -> str:
    material = (
        f"{email.casefold().strip()}\0{role.strip()}\0{organization_id.strip()}".encode("utf-8")
    )
    return hashlib.sha256(material).hexdigest()


def _login_key(identifier_digest: str) -> str:
    return f"{KEY_PREFIX}:login:{identifier_digest}"


def _session_key(jti: str) -> str:
    if not SAFE_JTI_RE.fullmatch(jti):
        raise SessionInactiveError("Session identifier is invalid")
    return f"{KEY_PREFIX}:session:{jti}"


def _parse_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(value.strip())
    except ValueError:
        return None


def _peer_is_trusted_proxy(peer: str) -> bool:
    """Return True only when the immediate TCP peer is an explicitly trusted proxy."""
    settings = get_settings()
    entries = [item.strip() for item in settings.auth_trusted_proxies.split(",") if item.strip()]
    if not entries:
        return False

    peer_ip = _parse_ip(peer)
    for entry in entries:
        if "/" in entry:
            try:
                network = ipaddress.ip_network(entry, strict=False)
            except ValueError:
                continue
            if peer_ip is not None and peer_ip in network:
                return True
            continue

        entry_ip = _parse_ip(entry)
        if entry_ip is not None:
            if peer_ip is not None and peer_ip == entry_ip:
                return True
            continue

        # Hostname entries (for example Compose service name "nginx") resolve at request time.
        try:
            resolved = socket.getaddrinfo(entry, None)
        except OSError:
            continue
        for item in resolved:
            host = item[4][0]
            if host == peer:
                return True
            resolved_ip = _parse_ip(host)
            if peer_ip is not None and resolved_ip is not None and peer_ip == resolved_ip:
                return True
    return False


def resolve_login_client_host(*, peer_host: str, x_real_ip: str | None, x_forwarded_for: str | None) -> str:
    """Derive the login-rate client identity behind an explicit trusted-proxy boundary.

    Decision:
    - The immediate TCP peer (`request.client.host`) is always the baseline.
    - Forwarding headers are ignored unless that peer is listed in AUTH_TRUSTED_PROXIES.
    - When the peer is trusted, only nginx-controlled `X-Real-IP` is accepted.
      Client-supplied `X-Forwarded-For` is never used to select the rate-limit key, so a
      spoofed XFF from an untrusted or even trusted hop cannot replace the identity.
    - Direct uvicorn access (empty AUTH_TRUSTED_PROXIES) therefore keys solely on peer IP.
    """
    peer = (peer_host or "unknown").strip() or "unknown"
    if not _peer_is_trusted_proxy(peer):
        return peer

    real_ip = (x_real_ip or "").strip()
    if real_ip and _parse_ip(real_ip) is not None:
        return real_ip
    # Intentionally ignore x_forwarded_for for keying.
    _ = x_forwarded_for
    return peer


def configure_auth_state_client(client: Redis | object | None) -> None:
    """Test/helper hook for injecting a Redis client or clearing it."""
    global _client, _auth_state_ready
    _client = client
    _auth_state_ready = False


def _require_client() -> Redis:
    if _client is None:
        raise AuthStateUnavailableError("Authentication state service unavailable")
    return _client  # type: ignore[return-value]


def _parse_failure_count(raw: object) -> int:
    if raw is None:
        return 0
    if isinstance(raw, bool):
        raise AuthStateUnavailableError("Authentication state service unavailable")
    if isinstance(raw, int):
        if raw < 0:
            raise AuthStateUnavailableError("Authentication state service unavailable")
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text or not text.isdigit():
            raise AuthStateUnavailableError("Authentication state service unavailable")
        value = int(text)
        if value < 0:
            raise AuthStateUnavailableError("Authentication state service unavailable")
        return value
    raise AuthStateUnavailableError("Authentication state service unavailable")


async def init_auth_state_store() -> None:
    """Create the shared Redis client and verify connectivity when auth is enabled."""
    global _client, _auth_state_ready
    settings = get_settings()
    if not settings.auth_enabled:
        _auth_state_ready = False
        return
    if _client is None:
        _client = Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=settings.redis_socket_connect_timeout_seconds,
            socket_timeout=settings.redis_socket_timeout_seconds,
            decode_responses=True,
        )
    try:
        await _client.ping()
    except Exception as exc:
        _auth_state_ready = False
        raise AuthStateUnavailableError("Authentication state service unavailable") from exc
    _auth_state_ready = True


async def close_auth_state_store() -> None:
    global _client, _auth_state_ready
    client = _client
    _client = None
    _auth_state_ready = False
    if client is None:
        return
    try:
        await client.aclose()
    except Exception:
        pass


async def ping_auth_state_store() -> bool:
    try:
        client = _require_client()
        await client.ping()
        return True
    except Exception:
        return False


async def get_login_failure_count(identifier_digest: str) -> int:
    client = _require_client()
    try:
        raw = await client.get(_login_key(identifier_digest))
    except AuthStateUnavailableError:
        raise
    except Exception as exc:
        raise AuthStateUnavailableError("Authentication state service unavailable") from exc
    return _parse_failure_count(raw)


async def record_login_failure(identifier_digest: str) -> int:
    client = _require_client()
    key = _login_key(identifier_digest)
    try:
        async with client.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, LOGIN_WINDOW_SECONDS, nx=True)
            count, _expire_ok = await pipe.execute()
    except Exception as exc:
        raise AuthStateUnavailableError("Authentication state service unavailable") from exc
    try:
        return _parse_failure_count(count)
    except AuthStateUnavailableError:
        raise
    except Exception as exc:
        raise AuthStateUnavailableError("Authentication state service unavailable") from exc


async def complete_successful_login(
    *,
    identifier_digest: str,
    jti: str,
    fingerprint: str,
    ttl_seconds: int,
) -> None:
    """Atomically register the session and clear the login-failure counter."""
    if ttl_seconds <= 0:
        raise AuthStateUnavailableError("Authentication state service unavailable")
    client = _require_client()
    session_key = _session_key(jti)
    login_key = _login_key(identifier_digest)
    try:
        async with client.pipeline(transaction=True) as pipe:
            pipe.set(session_key, fingerprint, ex=ttl_seconds)
            pipe.delete(login_key)
            await pipe.execute()
    except SessionInactiveError:
        raise
    except Exception as exc:
        raise AuthStateUnavailableError("Authentication state service unavailable") from exc


async def validate_session(*, jti: str, fingerprint: str) -> None:
    client = _require_client()
    try:
        key = _session_key(jti)
        stored = await client.get(key)
    except SessionInactiveError:
        raise
    except Exception as exc:
        raise AuthStateUnavailableError("Authentication state service unavailable") from exc
    if stored is None:
        raise SessionInactiveError("Session is inactive")
    if not isinstance(stored, str):
        stored = str(stored)
    if not hmac.compare_digest(stored, fingerprint):
        raise SessionInactiveError("Session claims do not match")


async def revoke_session(*, jti: str) -> None:
    client = _require_client()
    try:
        await client.delete(_session_key(jti))
    except SessionInactiveError:
        # Invalid jti shape is treated as already inactive for logout idempotency.
        return
    except Exception as exc:
        raise AuthStateUnavailableError("Authentication state service unavailable") from exc


def remaining_session_ttl_seconds(expires_at: datetime) -> int:
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    remaining = int((expires_at - datetime.now(timezone.utc)).total_seconds())
    return max(1, remaining)
