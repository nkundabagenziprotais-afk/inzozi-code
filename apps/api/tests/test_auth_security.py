from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.core.config import get_settings
from app.main import lifespan as app_lifespan
from app.routes.auth import router as auth_router
from app.routes.health import router as health_router
from app.security.auth import (
    SESSION_COOKIE,
    AuthenticationError,
    AuthMiddleware,
    ROLE_PERMISSIONS,
    create_session_token,
    decode_session_token,
    hash_password,
    permission_for_request,
    verify_password,
)
from app.security.redis_controls import (
    AuthStateUnavailableError,
    KEY_PREFIX,
    configure_auth_state_client,
    session_claims_fingerprint,
)
from tests.fake_redis import FakeAsyncRedis


def _configure_auth(monkeypatch, *, role: str = "platform_owner") -> FakeAsyncRedis:
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_BOOTSTRAP_EMAIL", "owner@inzozidigital.com")
    monkeypatch.setenv("AUTH_PASSWORD_HASH", hash_password("a-secure-staging-password", salt=b"1" * 16))
    monkeypatch.setenv("AUTH_SESSION_SECRET", "s" * 64)
    monkeypatch.setenv("AUTH_BOOTSTRAP_ROLE", role)
    monkeypatch.setenv("AUTH_ORGANIZATION_ID", "inzozi-digital")
    monkeypatch.setenv("AUTH_SESSION_TTL_MINUTES", "60")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    monkeypatch.setenv("AUTH_TRUSTED_PROXIES", "")
    monkeypatch.setenv("REDIS_URL", "redis://fake-redis/0")
    get_settings.cache_clear()
    fake = FakeAsyncRedis()
    configure_auth_state_client(fake)
    return fake


def _test_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(AuthMiddleware)
    app.include_router(auth_router)
    app.include_router(health_router)

    @app.get("/v1/workspaces/runtime")
    def read_workspace() -> dict:
        return {"status": "readable"}

    @app.post("/v1/workspaces")
    def create_workspace() -> dict:
        return {"status": "created"}

    return app


def test_password_hash_is_salted_and_verifiable():
    first = hash_password("a-secure-staging-password", salt=b"a" * 16)
    second = hash_password("a-secure-staging-password", salt=b"b" * 16)
    assert first != second
    assert verify_password("a-secure-staging-password", first) is True
    assert verify_password("wrong-password-value", first) is False
    assert first.startswith("pbkdf2_sha256$600000$")


def test_session_signature_rejects_tampering(monkeypatch):
    _configure_auth(monkeypatch)
    token, _ = create_session_token("owner@inzozidigital.com", "platform_owner", "inzozi-digital")
    principal = decode_session_token(token)
    assert principal.role == "platform_owner"
    assert principal.has("git:push") is True

    payload, signature = token.split(".", 1)
    replacement = "A" if signature[0] != "A" else "B"
    tampered = f"{payload}.{replacement}{signature[1:]}"
    with pytest.raises(AuthenticationError):
        decode_session_token(tampered)
    get_settings.cache_clear()
    configure_auth_state_client(None)


def test_permission_mapping_keeps_git_gates_separate():
    assert permission_for_request("POST", "/v1/workspaces/" + "a" * 32 + "/git/commit/prepare") == "git:commit"
    assert permission_for_request("POST", "/v1/workspaces/" + "a" * 32 + "/git/push/prepare") == "git:push"
    assert permission_for_request("POST", "/v1/workspaces/" + "a" * 32 + "/git/pull-request/prepare") == "git:pull_request"
    assert permission_for_request("PUT", "/v1/workspaces/" + "a" * 32 + "/files/README.md") == "workspace:edit"
    assert "deployment:approve" not in ROLE_PERMISSIONS["developer"]
    assert "git:push" not in ROLE_PERMISSIONS["reviewer"]


def test_login_issues_httponly_session_and_me_returns_role(monkeypatch):
    fake = _configure_auth(monkeypatch)
    with TestClient(_test_app()) as client:
        assert client.get("/v1/auth/me").status_code == 401
        response = client.post(
            "/v1/auth/login",
            json={"email": "owner@inzozidigital.com", "password": "a-secure-staging-password"},
        )
        assert response.status_code == 200
        assert "HttpOnly" in response.headers.get("set-cookie", "")
        assert "SameSite=strict" in response.headers.get("set-cookie", "")
        me = client.get("/v1/auth/me")
        assert me.status_code == 200
        assert me.json()["role"] == "platform_owner"
        assert me.json()["organization_id"] == "inzozi-digital"
        session_keys = [key for key in fake._values if key.startswith(f"{KEY_PREFIX}:session:")]
        assert len(session_keys) == 1
    get_settings.cache_clear()
    configure_auth_state_client(None)


def test_reviewer_can_read_but_direct_workspace_create_is_forbidden(monkeypatch):
    _configure_auth(monkeypatch, role="reviewer")
    with TestClient(_test_app()) as client:
        login = client.post(
            "/v1/auth/login",
            json={"email": "owner@inzozidigital.com", "password": "a-secure-staging-password"},
        )
        assert login.status_code == 200
        assert client.get("/v1/workspaces/runtime").status_code == 200
        denied = client.post("/v1/workspaces")
        assert denied.status_code == 403
        assert denied.json()["detail"] == "Your role does not allow this action"
    get_settings.cache_clear()
    configure_auth_state_client(None)


def test_five_failures_then_sixth_is_rate_limited(monkeypatch):
    fake = _configure_auth(monkeypatch)
    with TestClient(_test_app()) as client:
        for _ in range(5):
            failed = client.post(
                "/v1/auth/login",
                json={"email": "owner@inzozidigital.com", "password": "wrong-password-value"},
            )
            assert failed.status_code == 401
        login_keys = [key for key in fake._values if key.startswith(f"{KEY_PREFIX}:login:")]
        assert len(login_keys) == 1
        assert fake._values[login_keys[0]] == "5"
        blocked = client.post(
            "/v1/auth/login",
            json={"email": "owner@inzozidigital.com", "password": "a-secure-staging-password"},
        )
        assert blocked.status_code == 429
    get_settings.cache_clear()
    configure_auth_state_client(None)


def test_successful_login_clears_failure_counter(monkeypatch):
    fake = _configure_auth(monkeypatch)
    with TestClient(_test_app()) as client:
        for _ in range(3):
            assert (
                client.post(
                    "/v1/auth/login",
                    json={"email": "owner@inzozidigital.com", "password": "wrong-password-value"},
                ).status_code
                == 401
            )
        assert any(key.startswith(f"{KEY_PREFIX}:login:") for key in fake._values)
        ok = client.post(
            "/v1/auth/login",
            json={"email": "owner@inzozidigital.com", "password": "a-secure-staging-password"},
        )
        assert ok.status_code == 200
        assert not any(key.startswith(f"{KEY_PREFIX}:login:") for key in fake._values)
    get_settings.cache_clear()
    configure_auth_state_client(None)


def test_login_failure_state_survives_app_reconstruction(monkeypatch):
    fake = _configure_auth(monkeypatch)
    with TestClient(_test_app()) as client:
        for _ in range(5):
            assert (
                client.post(
                    "/v1/auth/login",
                    json={"email": "owner@inzozidigital.com", "password": "wrong-password-value"},
                ).status_code
                == 401
            )

    # Reconstruct FastAPI app while preserving the durable Redis stand-in.
    configure_auth_state_client(fake)
    with TestClient(_test_app()) as client:
        blocked = client.post(
            "/v1/auth/login",
            json={"email": "owner@inzozidigital.com", "password": "a-secure-staging-password"},
        )
        assert blocked.status_code == 429
    get_settings.cache_clear()
    configure_auth_state_client(None)


def test_active_session_survives_app_reconstruction(monkeypatch):
    fake = _configure_auth(monkeypatch)
    with TestClient(_test_app()) as client:
        login = client.post(
            "/v1/auth/login",
            json={"email": "owner@inzozidigital.com", "password": "a-secure-staging-password"},
        )
        assert login.status_code == 200
        assert client.get("/v1/auth/me").status_code == 200
        cookie = client.cookies.get(SESSION_COOKIE)
        assert cookie

    configure_auth_state_client(fake)
    with TestClient(_test_app()) as client:
        client.cookies.set(SESSION_COOKIE, cookie)
        me = client.get("/v1/auth/me")
        assert me.status_code == 200
        assert me.json()["email"] == "owner@inzozidigital.com"
    get_settings.cache_clear()
    configure_auth_state_client(None)


def test_logout_revokes_session_and_replay_is_rejected(monkeypatch):
    fake = _configure_auth(monkeypatch)
    with TestClient(_test_app()) as client:
        login = client.post(
            "/v1/auth/login",
            json={"email": "owner@inzozidigital.com", "password": "a-secure-staging-password"},
        )
        assert login.status_code == 200
        cookie = client.cookies.get(SESSION_COOKIE)
        assert cookie
        assert client.post("/v1/auth/logout").status_code == 204
        assert not any(key.startswith(f"{KEY_PREFIX}:session:") for key in fake._values)
        client.cookies.set(SESSION_COOKIE, cookie)
        replay = client.get("/v1/auth/me")
        assert replay.status_code == 401

    configure_auth_state_client(fake)
    with TestClient(_test_app()) as client:
        client.cookies.set(SESSION_COOKIE, cookie)
        assert client.get("/v1/auth/me").status_code == 401
    get_settings.cache_clear()
    configure_auth_state_client(None)


def test_missing_redis_session_rejects_otherwise_valid_cookie(monkeypatch):
    fake = _configure_auth(monkeypatch)
    token, _ = create_session_token("owner@inzozidigital.com", "platform_owner", "inzozi-digital")
    with TestClient(_test_app()) as client:
        client.cookies.set(SESSION_COOKIE, token)
        response = client.get("/v1/auth/me")
        assert response.status_code == 401
        assert response.json()["detail"] == "Session expired or invalid"
    assert fake._values == {}
    get_settings.cache_clear()
    configure_auth_state_client(None)


def test_mismatched_session_fingerprint_is_rejected(monkeypatch):
    fake = _configure_auth(monkeypatch)
    token, _expires_at = create_session_token("owner@inzozidigital.com", "platform_owner", "inzozi-digital")
    principal = decode_session_token(token)
    wrong = session_claims_fingerprint(
        email="other@inzozidigital.com",
        role=principal.role,
        organization_id=principal.organization_id,
    )
    fake._values[f"{KEY_PREFIX}:session:{principal.session_id}"] = wrong
    with TestClient(_test_app()) as client:
        client.cookies.set(SESSION_COOKIE, token)
        response = client.get("/v1/auth/me")
        assert response.status_code == 401
    assert any(key.startswith(f"{KEY_PREFIX}:session:") for key in fake._values)
    get_settings.cache_clear()
    configure_auth_state_client(None)


def test_redis_unavailable_during_login_returns_503(monkeypatch):
    fake = _configure_auth(monkeypatch)
    fake.fail_closed = True
    with TestClient(_test_app()) as client:
        response = client.post(
            "/v1/auth/login",
            json={"email": "owner@inzozidigital.com", "password": "a-secure-staging-password"},
        )
        assert response.status_code == 503
        assert response.json()["detail"] == "Authentication state service unavailable"
    get_settings.cache_clear()
    configure_auth_state_client(None)


def test_redis_unavailable_during_authenticated_request_returns_503(monkeypatch):
    fake = _configure_auth(monkeypatch)
    with TestClient(_test_app()) as client:
        login = client.post(
            "/v1/auth/login",
            json={"email": "owner@inzozidigital.com", "password": "a-secure-staging-password"},
        )
        assert login.status_code == 200
        fake.fail_closed = True
        response = client.get("/v1/auth/me")
        assert response.status_code == 503
        assert response.json()["detail"] == "Authentication state service unavailable"
    get_settings.cache_clear()
    configure_auth_state_client(None)


def test_expired_session_ttl_is_rejected(monkeypatch):
    fake = _configure_auth(monkeypatch)
    with TestClient(_test_app()) as client:
        login = client.post(
            "/v1/auth/login",
            json={"email": "owner@inzozidigital.com", "password": "a-secure-staging-password"},
        )
        assert login.status_code == 200
        session_keys = [key for key in fake._values if key.startswith(f"{KEY_PREFIX}:session:")]
        assert len(session_keys) == 1
        fake.force_expire(session_keys[0])
        response = client.get("/v1/auth/me")
        assert response.status_code == 401
    get_settings.cache_clear()
    configure_auth_state_client(None)


def test_auth_enabled_main_lifespan_fails_closed_without_redis(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_BOOTSTRAP_EMAIL", "owner@inzozidigital.com")
    monkeypatch.setenv("AUTH_PASSWORD_HASH", hash_password("a-secure-staging-password", salt=b"1" * 16))
    monkeypatch.setenv("AUTH_SESSION_SECRET", "s" * 64)
    monkeypatch.setenv("AUTH_BOOTSTRAP_ROLE", "platform_owner")
    monkeypatch.setenv("AUTH_ORGANIZATION_ID", "inzozi-digital")
    monkeypatch.setenv("WORKSPACE_OWNERSHIP_ENFORCED", "false")
    get_settings.cache_clear()
    fake = FakeAsyncRedis()
    fake.fail_closed = True
    configure_auth_state_client(fake)

    app = FastAPI(lifespan=app_lifespan)
    with pytest.raises(RuntimeError, match="Authentication state service unavailable"):
        with TestClient(app):
            pass
    # Client cleanup must still have run after startup failure.
    assert fake._closed is True
    get_settings.cache_clear()
    configure_auth_state_client(None)


def test_auth_disabled_main_lifespan_does_not_require_redis(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("WORKSPACE_OWNERSHIP_ENFORCED", "false")
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")
    get_settings.cache_clear()
    configure_auth_state_client(None)

    app = FastAPI(lifespan=app_lifespan)
    app.include_router(health_router)
    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["auth_enabled"] is False
        assert "auth_state_store" not in health.json()
        ready = client.get("/ready")
        assert ready.status_code == 200
        assert ready.json()["auth_state_store"] == "disabled"
    get_settings.cache_clear()
    configure_auth_state_client(None)


def test_corrupt_login_counter_fails_closed_with_503(monkeypatch):
    fake = _configure_auth(monkeypatch)
    with TestClient(_test_app()) as client:
        # Force a first failure so a login key exists, then corrupt it.
        assert (
            client.post(
                "/v1/auth/login",
                json={"email": "owner@inzozidigital.com", "password": "wrong-password-value"},
            ).status_code
            == 401
        )
        login_keys = [key for key in fake._values if key.startswith(f"{KEY_PREFIX}:login:")]
        assert len(login_keys) == 1
        fake._values[login_keys[0]] = "not-an-integer"
        response = client.post(
            "/v1/auth/login",
            json={"email": "owner@inzozidigital.com", "password": "a-secure-staging-password"},
        )
        assert response.status_code == 503
        assert response.json()["detail"] == "Authentication state service unavailable"
    get_settings.cache_clear()
    configure_auth_state_client(None)


def test_negative_login_counter_fails_closed(monkeypatch):
    import asyncio

    from app.security.redis_controls import get_login_failure_count

    fake = _configure_auth(monkeypatch)
    fake._values[f"{KEY_PREFIX}:login:abc"] = "-3"
    with pytest.raises(AuthStateUnavailableError):
        asyncio.run(get_login_failure_count("abc"))
    get_settings.cache_clear()
    configure_auth_state_client(None)


def test_atomic_increment_beyond_limit_returns_429(monkeypatch):
    fake = _configure_auth(monkeypatch)
    with TestClient(_test_app()) as client:
        for _ in range(5):
            assert (
                client.post(
                    "/v1/auth/login",
                    json={"email": "owner@inzozidigital.com", "password": "wrong-password-value"},
                ).status_code
                == 401
            )
        login_keys = [key for key in fake._values if key.startswith(f"{KEY_PREFIX}:login:")]
        assert fake._values[login_keys[0]] == "5"

        # Bypass the pre-check so the request races into record_login_failure after the limit.
        async def bypass_precheck(_digest: str) -> int:
            return 0

        monkeypatch.setattr("app.routes.auth.get_login_failure_count", bypass_precheck)
        overflow = client.post(
            "/v1/auth/login",
            json={"email": "owner@inzozidigital.com", "password": "wrong-password-value"},
        )
        assert overflow.status_code == 429
        assert int(fake._values[login_keys[0]]) == 6
    get_settings.cache_clear()
    configure_auth_state_client(None)


def test_concurrent_login_failures_cannot_bypass_limit(monkeypatch):
    import asyncio

    from httpx import ASGITransport, AsyncClient

    fake = _configure_auth(monkeypatch)
    app = _test_app()

    async def seed_four_failures() -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            for _ in range(4):
                response = await client.post(
                    "/v1/auth/login",
                    json={"email": "owner@inzozidigital.com", "password": "wrong-password-value"},
                )
                assert response.status_code == 401

    async def racing_attempt() -> int:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/auth/login",
                json={"email": "owner@inzozidigital.com", "password": "wrong-password-value"},
            )
            return response.status_code

    async def run_race() -> list[int]:
        await seed_four_failures()
        # Keep pre-check from short-circuiting the race so concurrent INCR values are observed.
        async def stale_precheck(_digest: str) -> int:
            return 4

        monkeypatch.setattr("app.routes.auth.get_login_failure_count", stale_precheck)
        return list(await asyncio.gather(*[racing_attempt() for _ in range(8)]))

    statuses = asyncio.run(run_race())
    assert all(status in {401, 429} for status in statuses)
    assert 429 in statuses
    login_keys = [key for key in fake._values if key.startswith(f"{KEY_PREFIX}:login:")]
    assert int(fake._values[login_keys[0]]) > 5
    get_settings.cache_clear()
    configure_auth_state_client(None)


def test_failed_login_transaction_does_not_clear_throttle_without_session(monkeypatch):
    fake = _configure_auth(monkeypatch)
    with TestClient(_test_app()) as client:
        for _ in range(3):
            assert (
                client.post(
                    "/v1/auth/login",
                    json={"email": "owner@inzozidigital.com", "password": "wrong-password-value"},
                ).status_code
                == 401
            )
        assert any(key.startswith(f"{KEY_PREFIX}:login:") for key in fake._values)
        fake.fail_pipeline_execute = True
        response = client.post(
            "/v1/auth/login",
            json={"email": "owner@inzozidigital.com", "password": "a-secure-staging-password"},
        )
        assert response.status_code == 503
        assert "set-cookie" not in {k.lower() for k in response.headers.keys()}
        assert any(key.startswith(f"{KEY_PREFIX}:login:") for key in fake._values)
        assert not any(key.startswith(f"{KEY_PREFIX}:session:") for key in fake._values)
    get_settings.cache_clear()
    configure_auth_state_client(None)


def test_untrusted_peer_cannot_spoof_forwarding_headers(monkeypatch):
    from app.security.redis_controls import login_identifier_digest, resolve_login_client_host

    _configure_auth(monkeypatch)
    monkeypatch.setenv("AUTH_TRUSTED_PROXIES", "10.10.0.2")
    get_settings.cache_clear()

    spoofed = resolve_login_client_host(
        peer_host="203.0.113.50",
        x_real_ip="198.51.100.10",
        x_forwarded_for="198.51.100.10, 203.0.113.50",
    )
    assert spoofed == "203.0.113.50"
    digest_spoofed = login_identifier_digest(client_host=spoofed, email="owner@inzozidigital.com")
    digest_real_header = login_identifier_digest(client_host="198.51.100.10", email="owner@inzozidigital.com")
    assert digest_spoofed != digest_real_header

    trusted = resolve_login_client_host(
        peer_host="10.10.0.2",
        x_real_ip="198.51.100.10",
        x_forwarded_for="1.2.3.4, 198.51.100.10",
    )
    assert trusted == "198.51.100.10"

    trusted_without_real_ip = resolve_login_client_host(
        peer_host="10.10.0.2",
        x_real_ip=None,
        x_forwarded_for="1.2.3.4",
    )
    assert trusted_without_real_ip == "10.10.0.2"
    get_settings.cache_clear()
    configure_auth_state_client(None)


def test_ready_endpoint_reports_auth_store_outage(monkeypatch):
    fake = _configure_auth(monkeypatch)
    with TestClient(_test_app()) as client:
        assert client.get("/ready").status_code == 200
        assert client.get("/ready").json()["auth_state_store"] == "ready"
        fake.fail_closed = True
        ready = client.get("/ready")
        assert ready.status_code == 503
        assert ready.json()["status"] == "not_ready"
        assert ready.json()["auth_state_store"] == "unavailable"
        # Liveness remains up.
        assert client.get("/health").status_code == 200
        assert "auth_state_store" not in client.get("/health").json()
    get_settings.cache_clear()
    configure_auth_state_client(None)


def test_login_runs_blocking_work_in_threadpool(monkeypatch):
    from app.security.redis_controls import resolve_login_client_host

    _configure_auth(monkeypatch)
    seen: list[object] = []

    async def tracking_run_in_threadpool(func, *args, **kwargs):
        seen.append(func)
        return func(*args, **kwargs)

    monkeypatch.setattr("app.routes.auth.run_in_threadpool", tracking_run_in_threadpool)

    with TestClient(_test_app()) as client:
        wrong_user = client.post(
            "/v1/auth/login",
            json={"email": "nobody@example.com", "password": "a-secure-staging-password"},
        )
        assert wrong_user.status_code == 401
        ok = client.post(
            "/v1/auth/login",
            json={"email": "owner@inzozidigital.com", "password": "a-secure-staging-password"},
        )
        assert ok.status_code == 200

    assert resolve_login_client_host in seen
    assert verify_password in seen
    assert set(seen) == {resolve_login_client_host, verify_password}
    get_settings.cache_clear()
    configure_auth_state_client(None)


def test_api_dockerfile_disables_uvicorn_proxy_header_rewriting():
    from pathlib import Path

    dockerfile = Path(__file__).resolve().parents[1] / "Dockerfile"
    text = dockerfile.read_text(encoding="utf-8")
    assert "--no-proxy-headers" in text
    assert "CMD" in text
    assert "uvicorn" in text


def test_login_marks_session_cookie_secure_when_enabled(monkeypatch):
    _configure_auth(monkeypatch)
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "true")
    get_settings.cache_clear()

    with TestClient(_test_app()) as client:
        response = client.post(
            "/v1/auth/login",
            json={
                "email": "owner@inzozidigital.com",
                "password": "a-secure-staging-password",
            },
        )

        assert response.status_code == 200

        set_cookie = response.headers.get("set-cookie", "")

        assert "HttpOnly" in set_cookie
        assert "SameSite=strict" in set_cookie
        assert "Secure" in set_cookie

    get_settings.cache_clear()
    configure_auth_state_client(None)
