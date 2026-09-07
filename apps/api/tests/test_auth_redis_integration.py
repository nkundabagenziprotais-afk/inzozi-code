from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest
from redis.asyncio import Redis

from app.core.config import get_settings
from app.routes.auth import router as auth_router
from app.routes.health import router as health_router
from app.security.auth import SESSION_COOKIE, AuthMiddleware, hash_password
from app.security.redis_controls import (
    KEY_PREFIX,
    close_auth_state_store,
    complete_successful_login,
    configure_auth_state_client,
    get_login_failure_count,
    init_auth_state_store,
    record_login_failure,
    validate_session,
)

RUN_REDIS = os.getenv("RUN_REDIS_INTEGRATION") == "1"
REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/15")

pytestmark = pytest.mark.skipif(not RUN_REDIS, reason="Set RUN_REDIS_INTEGRATION=1 with Redis available")


def _run(coro):
    """Each test owns one event loop for Redis client create/use/close."""
    return asyncio.run(coro)


def _configure(monkeypatch) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_BOOTSTRAP_EMAIL", "owner@inzozidigital.com")
    monkeypatch.setenv("AUTH_PASSWORD_HASH", hash_password("a-secure-staging-password", salt=b"9" * 16))
    monkeypatch.setenv("AUTH_SESSION_SECRET", "r" * 64)
    monkeypatch.setenv("AUTH_BOOTSTRAP_ROLE", "platform_owner")
    monkeypatch.setenv("AUTH_ORGANIZATION_ID", "inzozi-digital")
    monkeypatch.setenv("AUTH_SESSION_TTL_MINUTES", "60")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    monkeypatch.setenv("AUTH_TRUSTED_PROXIES", "")
    monkeypatch.setenv("REDIS_URL", REDIS_URL)
    get_settings.cache_clear()


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(AuthMiddleware)
    app.include_router(auth_router)
    app.include_router(health_router)
    return app


@asynccontextmanager
async def _owned_redis():
    client = Redis.from_url(
        REDIS_URL,
        decode_responses=True,
        socket_connect_timeout=1.0,
        socket_timeout=1.0,
    )
    try:
        await client.ping()
        await client.flushdb()
        configure_auth_state_client(client)
        await init_auth_state_store()
        yield client
    finally:
        try:
            await client.flushdb()
        finally:
            await close_auth_state_store()
            get_settings.cache_clear()


def test_real_redis_failure_counter_transaction_and_ttl(monkeypatch):
    _configure(monkeypatch)
    digest = "a" * 64

    async def body():
        async with _owned_redis() as redis_client:
            assert await get_login_failure_count(digest) == 0
            assert await record_login_failure(digest) == 1
            ttl = await redis_client.ttl(f"{KEY_PREFIX}:login:{digest}")
            assert 1 <= ttl <= 300
            for expected in range(2, 6):
                assert await record_login_failure(digest) == expected
            assert await record_login_failure(digest) == 6

    _run(body())


def test_real_redis_successful_login_is_atomic(monkeypatch):
    _configure(monkeypatch)
    digest = "b" * 64

    async def body():
        async with _owned_redis() as redis_client:
            await record_login_failure(digest)
            await record_login_failure(digest)
            await complete_successful_login(
                identifier_digest=digest,
                jti="integration-session-jti-001",
                fingerprint="f" * 64,
                ttl_seconds=120,
            )
            assert await get_login_failure_count(digest) == 0
            assert await redis_client.get(f"{KEY_PREFIX}:login:{digest}") is None
            assert await redis_client.get(f"{KEY_PREFIX}:session:integration-session-jti-001") == "f" * 64
            session_ttl = await redis_client.ttl(f"{KEY_PREFIX}:session:integration-session-jti-001")
            assert 1 <= session_ttl <= 120
            await validate_session(jti="integration-session-jti-001", fingerprint="f" * 64)

    _run(body())


def test_real_redis_http_login_logout_roundtrip(monkeypatch):
    _configure(monkeypatch)

    async def body():
        async with _owned_redis():
            transport = ASGITransport(app=_app())
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                login = await client.post(
                    "/v1/auth/login",
                    json={"email": "owner@inzozidigital.com", "password": "a-secure-staging-password"},
                )
                assert login.status_code == 200
                assert (await client.get("/v1/auth/me")).status_code == 200
                assert (await client.get("/ready")).status_code == 200
                cookie = login.cookies.get(SESSION_COOKIE)
                assert cookie
                assert (await client.post("/v1/auth/logout")).status_code == 204
                assert (await client.get("/v1/auth/me", cookies={SESSION_COOKIE: cookie})).status_code == 401

    _run(body())


def test_real_redis_survives_application_reconstruction(monkeypatch):
    """Login throttling and sessions remain durable across app reconstruction."""
    _configure(monkeypatch)

    async def body():
        client = Redis.from_url(
            REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=1.0,
            socket_timeout=1.0,
        )
        try:
            await client.ping()
            await client.flushdb()
            configure_auth_state_client(client)
            await init_auth_state_store()

            transport = ASGITransport(app=_app())
            async with AsyncClient(transport=transport, base_url="http://test") as http:
                for _ in range(5):
                    failed = await http.post(
                        "/v1/auth/login",
                        json={"email": "owner@inzozidigital.com", "password": "wrong-password"},
                    )
                    assert failed.status_code == 401
                throttled = await http.post(
                    "/v1/auth/login",
                    json={"email": "owner@inzozidigital.com", "password": "wrong-password"},
                )
                assert throttled.status_code == 429

            # Reconstruct the FastAPI app while keeping Redis state on this same loop/client.
            configure_auth_state_client(client)
            await init_auth_state_store()
            transport = ASGITransport(app=_app())
            async with AsyncClient(transport=transport, base_url="http://test") as http:
                still_throttled = await http.post(
                    "/v1/auth/login",
                    json={"email": "owner@inzozidigital.com", "password": "a-secure-staging-password"},
                )
                assert still_throttled.status_code == 429

            await client.flushdb()
            configure_auth_state_client(client)
            await init_auth_state_store()

            transport = ASGITransport(app=_app())
            async with AsyncClient(transport=transport, base_url="http://test") as http:
                login = await http.post(
                    "/v1/auth/login",
                    json={"email": "owner@inzozidigital.com", "password": "a-secure-staging-password"},
                )
                assert login.status_code == 200
                cookie = login.cookies.get(SESSION_COOKIE)
                assert cookie
                assert (await http.get("/v1/auth/me")).status_code == 200

            configure_auth_state_client(client)
            await init_auth_state_store()
            transport = ASGITransport(app=_app())
            async with AsyncClient(transport=transport, base_url="http://test") as http:
                assert (await http.get("/v1/auth/me", cookies={SESSION_COOKIE: cookie})).status_code == 200
                assert (await http.post("/v1/auth/logout", cookies={SESSION_COOKIE: cookie})).status_code == 204

            configure_auth_state_client(client)
            await init_auth_state_store()
            transport = ASGITransport(app=_app())
            async with AsyncClient(transport=transport, base_url="http://test") as http:
                assert (await http.get("/v1/auth/me", cookies={SESSION_COOKIE: cookie})).status_code == 401
        finally:
            try:
                await client.flushdb()
            finally:
                await close_auth_state_store()
                get_settings.cache_clear()

    _run(body())
