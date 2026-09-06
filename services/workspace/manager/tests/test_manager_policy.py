import time

import pytest

from app.main import (
    GITHUB_HTTPS_RE,
    _is_expired,
    _network_name,
    _runtime_name,
    _volume_name,
    _workspace_id,
)


class FakeContainer:
    def __init__(self, expires_at: int):
        self.labels = {"com.inzozi.code.expires_at": str(expires_at)}


def test_workspace_resource_names_are_id_scoped():
    workspace_id = "a" * 32
    assert _runtime_name(workspace_id) == f"inzozi-ws-{workspace_id}"
    assert _volume_name(workspace_id) == f"inzozi-ws-vol-{workspace_id}"
    assert _network_name(workspace_id) == f"inzozi-ws-net-{workspace_id}"


def test_invalid_workspace_id_is_rejected():
    with pytest.raises(ValueError):
        _workspace_id("../../docker.sock")


def test_ttl_expiry_is_fail_closed():
    now = int(time.time())
    assert _is_expired(FakeContainer(now - 1)) is True
    assert _is_expired(FakeContainer(now + 60)) is False
    assert _is_expired(type("Broken", (), {"labels": {}})()) is True


def test_clone_allowlist_is_github_https_only():
    assert GITHUB_HTTPS_RE.fullmatch("https://github.com/inzozi/example")
    assert not GITHUB_HTTPS_RE.fullmatch("http://github.com/inzozi/example")
    assert not GITHUB_HTTPS_RE.fullmatch("https://example.com/inzozi/example")
    assert not GITHUB_HTTPS_RE.fullmatch("file:///etc/passwd")
