import asyncio

import app.integrations.github_app as github_app

REPOSITORY_URL = "https://github.com/nkundabagenziprotais-afk/inzozi-code"


class FakeResponse:
    def __init__(self, payload, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code
        self.is_error = status_code >= 400

    def json(self):
        return self._payload


def test_permission_readiness_requires_write_for_remote_gates():
    assert github_app.permission_readiness({"contents": "read", "pull_requests": "read"}) == {
        "private_clone": True,
        "remote_push": False,
        "draft_pull_request": False,
    }
    assert github_app.permission_readiness({"contents": "write", "pull_requests": "write"}) == {
        "private_clone": True,
        "remote_push": True,
        "draft_pull_request": True,
    }


def test_installation_summary_reports_safe_permission_metadata(monkeypatch):
    seen = {}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, **kwargs):
            seen.update({"url": url, **kwargs})
            return FakeResponse({
                "account": {"login": "nkundabagenziprotais-afk"},
                "repository_selection": "selected",
                "permissions": {"contents": "write", "pull_requests": "write"},
                "suspended_at": None,
            })

    monkeypatch.setattr(github_app, "create_app_jwt", lambda: "app-jwt")
    monkeypatch.setattr(github_app.httpx, "AsyncClient", FakeClient)

    summary = asyncio.run(github_app.get_installation_summary(456))
    assert summary["account"] == "nkundabagenziprotais-afk"
    assert summary["repository_selection"] == "selected"
    assert summary["readiness"] == {
        "private_clone": True,
        "remote_push": True,
        "draft_pull_request": True,
    }
    assert seen["headers"]["Authorization"] == "Bearer app-jwt"
    assert seen["headers"]["X-GitHub-Api-Version"] == github_app.GITHUB_API_VERSION


def test_verify_repository_access_returns_no_token(monkeypatch):
    async def fake_token(*args, **kwargs):
        assert args[0] == 456
        assert args[1] == REPOSITORY_URL
        assert kwargs["contents_permission"] == "read"
        return "ephemeral-installation-token"

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, **kwargs):
            assert url.endswith("/repos/nkundabagenziprotais-afk/inzozi-code")
            assert kwargs["headers"]["Authorization"] == "Bearer ephemeral-installation-token"
            return FakeResponse({
                "full_name": "nkundabagenziprotais-afk/inzozi-code",
                "private": True,
                "default_branch": "main",
            })

    monkeypatch.setattr(github_app, "create_installation_token", fake_token)
    monkeypatch.setattr(github_app.httpx, "AsyncClient", FakeClient)

    result = asyncio.run(github_app.verify_repository_access(REPOSITORY_URL, 456))
    assert result == {
        "full_name": "nkundabagenziprotais-afk/inzozi-code",
        "private": True,
        "default_branch": "main",
        "read_access_verified": True,
    }
    assert "token" not in result
