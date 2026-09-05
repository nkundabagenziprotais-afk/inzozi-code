from app.main import CreateWorkspaceRequest, PushRequest, SAFE_PUSH_BRANCH_RE, _git_auth_environment


def test_askpass_script_does_not_embed_token(tmp_path):
    token = "super-secret-installation-token"
    env, script = _git_auth_environment(tmp_path, token)

    assert token not in script.read_text(encoding="utf-8")
    assert env["INZOZI_GIT_TOKEN"] == token
    assert env["INZOZI_GIT_USERNAME"] == "x-access-token"


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
