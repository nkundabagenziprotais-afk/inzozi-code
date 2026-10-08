import time

import pytest

from app.main import (
    EGRESS_NETWORK,
    EGRESS_PROXY_URL,
    GITHUB_HTTPS_RE,
    SAFE_PUSH_BRANCH_RE,
    _helper_run,
    _is_expired,
    _network_name,
    _quota_helper_run,
    _quota_host_path,
    _review_protection_active,
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
    assert _quota_host_path(workspace_id).endswith(f"/{workspace_id}")


def test_invalid_workspace_id_is_rejected():
    with pytest.raises(ValueError):
        _workspace_id("../../docker.sock")
    with pytest.raises(ValueError):
        _quota_host_path("../../escape")


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


def test_push_branch_policy_remains_non_production_only():
    assert SAFE_PUSH_BRANCH_RE.fullmatch("feature/reviewed-change")
    assert SAFE_PUSH_BRANCH_RE.fullmatch("fix/login-loop")
    assert not SAFE_PUSH_BRANCH_RE.fullmatch("main")
    assert not SAFE_PUSH_BRANCH_RE.fullmatch("production")
    assert not SAFE_PUSH_BRANCH_RE.fullmatch("../../escape")


def test_broker_does_not_accept_arbitrary_helper_modules():
    with pytest.raises(RuntimeError, match="Unsupported workspace helper module"):
        _helper_run(
            module="os.system",
            environment={},
            volume_name="inzozi-ws-vol-" + "a" * 32,
            network_name=None,
        )


def test_broker_does_not_accept_arbitrary_quota_actions():
    with pytest.raises(RuntimeError, match="Unsupported quota helper action"):
        _quota_helper_run("shell", workspace_id="a" * 32)



def test_broker_rejects_arbitrary_helper_networks():
    with pytest.raises(
        RuntimeError,
        match="Unsupported workspace helper network",
    ):
        _helper_run(
            module="app.bootstrap",
            environment={},
            volume_name="inzozi-ws-vol-" + "a" * 32,
            network_name="bridge",
        )


def test_github_helpers_receive_fixed_proxy_environment(
    monkeypatch,
):
    import app.main as broker_main

    captured = {}

    def fake_run(**kwargs):
        captured.update(kwargs)
        return b"ok"

    monkeypatch.setattr(
        broker_main.docker_client.containers,
        "run",
        fake_run,
    )

    result = _helper_run(
        module="app.remote_push",
        environment={
            "WORKSPACE_BRANCH": "fix/example",
            "HTTPS_PROXY": "http://attacker.invalid:9999",
        },
        volume_name="inzozi-ws-vol-" + "a" * 32,
        network_name=EGRESS_NETWORK,
    )

    assert result == b"ok"

    env = captured["environment"]

    assert env["HTTPS_PROXY"] == EGRESS_PROXY_URL
    assert env["https_proxy"] == EGRESS_PROXY_URL
    assert env["HTTP_PROXY"] == EGRESS_PROXY_URL
    assert env["http_proxy"] == EGRESS_PROXY_URL
    assert env["NO_PROXY"] == ""
    assert env["no_proxy"] == ""


class FakeExecResult:
    def __init__(self, output: str, exit_code: int = 0):
        self.exit_code = exit_code
        self.output = output.encode("utf-8")


class FakeReviewContainer(FakeContainer):
    def __init__(self, expires_at: int, status_output: str, workspace_id: str = "a" * 32):
        super().__init__(expires_at)
        self.labels["com.inzozi.code.workspace_id"] = workspace_id
        self.status_output = status_output

    def exec_run(self, argv, stdout=True, stderr=True):
        assert argv[:3] == ["git", "-C", f"/workspaces/{'a' * 32}/repo"]
        return FakeExecResult(self.status_output)


def test_expired_dirty_workspace_is_protected_during_review_grace(monkeypatch):
    import app.main as broker_main

    monkeypatch.setattr(broker_main, "REVIEW_GRACE_SECONDS", 3600)
    now = int(time.time())
    dirty = FakeReviewContainer(now - 60, "## feature/review\n M app.py\n")
    assert _review_protection_active(dirty, now=now) is True


def test_expired_clean_workspace_is_not_review_protected(monkeypatch):
    import app.main as broker_main

    monkeypatch.setattr(broker_main, "REVIEW_GRACE_SECONDS", 3600)
    now = int(time.time())
    clean = FakeReviewContainer(now - 60, "## main...origin/main\n")
    assert _review_protection_active(clean, now=now) is False


def test_review_protection_has_a_hard_upper_bound(monkeypatch):
    import app.main as broker_main

    monkeypatch.setattr(broker_main, "REVIEW_GRACE_SECONDS", 300)
    now = int(time.time())
    dirty = FakeReviewContainer(now - 301, "## feature/review\n M app.py\n")
    assert _review_protection_active(dirty, now=now) is False
