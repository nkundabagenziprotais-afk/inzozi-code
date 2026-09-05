from app.main import CreateWorkspaceRequest, _git_auth_environment


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
