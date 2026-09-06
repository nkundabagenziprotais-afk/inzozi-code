import pytest

import app.integrations.github_app as github_app

REPOSITORY_URL = "https://github.com/nkundabagenziprotais-afk/inzozi-code"


class FakeResponse:
    def __init__(self, payload, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code
        self.is_error = status_code >= 400

    def json(self):
        return self._payload


@pytest.mark.asyncio
async def test_create_or_get_pull_request_reuses_existing(monkeypatch):
    calls = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, **kwargs):
            calls.append(("GET", url, kwargs))
            return FakeResponse([
                {
                    "number": 17,
                    "html_url": "https://github.com/nkundabagenziprotais-afk/inzozi-code/pull/17",
                    "draft": True,
                    "head": {"ref": "feature/reviewed-change"},
                    "base": {"ref": "main"},
                }
            ])

        async def post(self, url, **kwargs):
            calls.append(("POST", url, kwargs))
            raise AssertionError("Existing PR should prevent a create request")

    monkeypatch.setattr(github_app.httpx, "AsyncClient", FakeClient)
    result = await github_app.create_or_get_draft_pull_request(
        REPOSITORY_URL,
        branch="feature/reviewed-change",
        base_branch="main",
        title="feat: reviewed change",
        body="Reviewed body",
        token="short-lived-token",
    )
    assert result["status"] == "existing"
    assert result["number"] == 17
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_create_pull_request_is_always_draft_and_not_maintainer_mutable(monkeypatch):
    calls = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, **kwargs):
            calls.append(("GET", url, kwargs))
            return FakeResponse([])

        async def post(self, url, **kwargs):
            calls.append(("POST", url, kwargs))
            return FakeResponse({
                "number": 18,
                "html_url": "https://github.com/nkundabagenziprotais-afk/inzozi-code/pull/18",
                "draft": True,
            }, status_code=201)

    monkeypatch.setattr(github_app.httpx, "AsyncClient", FakeClient)
    result = await github_app.create_or_get_draft_pull_request(
        REPOSITORY_URL,
        branch="feature/reviewed-change",
        base_branch="main",
        title="feat: reviewed change",
        body="Reviewed body",
        token="short-lived-token",
    )
    assert result["status"] == "created"
    assert result["draft"] is True
    post_call = next(call for call in calls if call[0] == "POST")
    payload = post_call[2]["json"]
    assert payload["draft"] is True
    assert payload["maintainer_can_modify"] is False
    assert payload["head"] == "feature/reviewed-change"
    assert payload["base"] == "main"


@pytest.mark.asyncio
async def test_remote_branch_head_is_read_from_exact_ref(monkeypatch):
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
            return FakeResponse({"object": {"sha": "b" * 40}})

    monkeypatch.setattr(github_app.httpx, "AsyncClient", FakeClient)
    sha = await github_app.get_remote_branch_head(
        REPOSITORY_URL,
        "feature/reviewed-change",
        "short-lived-token",
    )
    assert sha == "b" * 40
    assert seen["url"].endswith("/git/ref/heads/feature/reviewed-change")
    assert seen["headers"]["Authorization"] == "Bearer short-lived-token"
