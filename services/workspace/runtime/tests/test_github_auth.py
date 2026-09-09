from pathlib import Path
import os
import subprocess

from app.git_credentials import GIT_ASKPASS_PATH, github_git_environment
from app.main import CreateWorkspaceRequest, PushRequest, SAFE_PUSH_BRANCH_RE, _git_auth_environment


def test_askpass_script_does_not_embed_token(tmp_path):
    token = "super-secret-installation-token"
    env, script = _git_auth_environment(tmp_path, token)

    assert token not in script.read_text(encoding="utf-8")
    assert env["INZOZI_GIT_TOKEN"] == token
    assert env["INZOZI_GIT_USERNAME"] == "x-access-token"


def test_dedicated_git_askpass_uses_fixed_image_path_outside_noexec_tmp():
    token = "short-lived-installation-token"
    env = github_git_environment(token)

    assert env["GIT_ASKPASS"] == GIT_ASKPASS_PATH
    assert not GIT_ASKPASS_PATH.startswith("/tmp/")
    assert env["GIT_ASKPASS_REQUIRE"] == "force"
    assert env["WORKSPACE_GIT_TOKEN"] == token


def test_fixed_askpass_script_reads_token_only_from_environment():
    script = Path(__file__).parents[1] / "app" / "git_askpass.sh"
    env = os.environ.copy()
    env["WORKSPACE_GIT_TOKEN"] = "fake-installation-token"

    username = subprocess.run(
        ["sh", str(script), "Username for https://github.com"],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    password = subprocess.run(
        ["sh", str(script), "Password for https://x-access-token@github.com"],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    assert username.stdout.strip() == "x-access-token"
    assert password.stdout.strip() == "fake-installation-token"
    assert "fake-installation-token" not in script.read_text(encoding="utf-8")


def test_workspace_request_hides_token_from_repr():
    request = CreateWorkspaceRequest(
        repository_url="https://github.com/example/private-repo",
        git_token="super-secret-installation-token",
    )
    assert "super-secret-installation-token" not in repr(request)


def test_push_request_hides_write_token_from_repr():
    request = PushRequest(
        branch="feature/reviewed-change",
        expected_head="a" * 40,
        git_token="short-lived-write-token",
    )
    assert "short-lived-write-token" not in repr(request)


def test_remote_push_accepts_only_safe_branches():
    assert SAFE_PUSH_BRANCH_RE.fullmatch("feature/reviewed-change")
    assert SAFE_PUSH_BRANCH_RE.fullmatch("fix/login-loop")
    assert not SAFE_PUSH_BRANCH_RE.fullmatch("main")
    assert not SAFE_PUSH_BRANCH_RE.fullmatch("Feature/Uppercase")
    assert not SAFE_PUSH_BRANCH_RE.fullmatch("../../escape")


def test_github_git_environment_preserves_restricted_proxy_without_copying_secrets(
    monkeypatch,
):
    proxy = "http://workspace-egress-proxy:3128"

    monkeypatch.setenv("HTTPS_PROXY", proxy)
    monkeypatch.setenv("https_proxy", proxy)
    monkeypatch.setenv("HTTP_PROXY", proxy)
    monkeypatch.setenv("http_proxy", proxy)
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("no_proxy", "")

    monkeypatch.setenv(
        "AUTH_SESSION_SECRET",
        "must-not-enter-git-environment",
    )
    monkeypatch.setenv(
        "DATABASE_URL",
        "must-not-enter-git-environment",
    )
    monkeypatch.setenv(
        "ALL_PROXY",
        "must-not-enter-git-environment",
    )

    env = github_git_environment(
        "short-lived-installation-token"
    )

    assert env["HTTPS_PROXY"] == proxy
    assert env["https_proxy"] == proxy
    assert env["HTTP_PROXY"] == proxy
    assert env["http_proxy"] == proxy
    assert env["NO_PROXY"] == ""
    assert env["no_proxy"] == ""

    assert (
        env["WORKSPACE_GIT_TOKEN"]
        == "short-lived-installation-token"
    )

    assert "AUTH_SESSION_SECRET" not in env
    assert "DATABASE_URL" not in env
    assert "ALL_PROXY" not in env
