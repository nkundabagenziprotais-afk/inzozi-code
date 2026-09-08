from __future__ import annotations

import importlib.util
import json
import sys
import types
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.security.auth import DUMMY_PASSWORD_HASH, SESSION_COOKIE, hash_password
from app.security.identity_store import AuthUser


REPO_ROOT = Path(__file__).resolve().parents[3]
IDENTITY_POISON = {
    "AUTH_PASSWORD_HASH": "pbkdf2_sha256$600000$poison$poison",
    "AUTH_SESSION_SECRET": "poison-session-secret-should-never-leak-0001",
    "invitation_token": "plaintext-invite-token-should-never-leak",
    "inzozi_session": "signed-session-cookie-should-never-leak",
    "password_hash": "pbkdf2_sha256$600000$leak$leak",
}


def _load_service_module(service_app_dir: Path, package_name: str, module_name: str):
    if package_name not in sys.modules:
        package = types.ModuleType(package_name)
        package.__path__ = [str(service_app_dir)]  # type: ignore[attr-defined]
        sys.modules[package_name] = package
    full_name = f"{package_name}.{module_name}"
    module_path = service_app_dir / f"{module_name}.py"
    spec = importlib.util.spec_from_file_location(full_name, module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    module.__package__ = package_name
    sys.modules[full_name] = module
    spec.loader.exec_module(module)
    return module


def test_broker_runtime_and_helper_env_builders_exclude_identity_secrets(monkeypatch):
    for key, value in IDENTITY_POISON.items():
        monkeypatch.setenv(key, value)

    builders = _load_service_module(
        REPO_ROOT / "services/workspace/broker/app",
        "inzozi_broker_app_for_secret_tests",
        "env_builders",
    )
    runtime_env = builders.runtime_container_environment(
        max_file_bytes="1048576",
        max_output_bytes="65536",
        max_search_file_bytes="524288",
        max_checkpoint_bytes="104857600",
    )
    bootstrap_env = builders.bootstrap_helper_environment(
        repository_url="https://github.com/example/private-repo",
        ref="main",
        git_token="ghs_workspace-only-token",
    )
    push_env = builders.push_helper_environment(
        branch="inzozi-code/feature",
        expected_head="a" * 40,
        git_token="ghs_workspace-only-token",
    )
    quota_env = builders.quota_helper_environment(workspace_id="a" * 32, limit_bytes=1024)

    for env in (runtime_env, bootstrap_env, push_env, quota_env):
        assert isinstance(env, dict)
        for poison_key, poison_value in IDENTITY_POISON.items():
            assert poison_key not in env
            assert poison_value not in env.values()
            assert poison_value not in json.dumps(env)
        assert "AUTH_SESSION_SECRET" not in env
        assert SESSION_COOKIE not in env
        assert DUMMY_PASSWORD_HASH not in env.values()

    assert set(runtime_env) == {
        "WORKSPACE_ROOT",
        "WORKSPACE_MAX_FILE_BYTES",
        "WORKSPACE_MAX_OUTPUT_BYTES",
        "WORKSPACE_MAX_SEARCH_FILE_BYTES",
        "WORKSPACE_MAX_CHECKPOINT_BYTES",
    }


def test_manager_broker_payloads_exclude_identity_secrets(monkeypatch):
    for key, value in IDENTITY_POISON.items():
        monkeypatch.setenv(key, value)
    payloads = _load_service_module(
        REPO_ROOT / "services/workspace/manager/app",
        "inzozi_manager_app_for_secret_tests",
        "broker_payloads",
    )
    create_payload = payloads.build_broker_create_workspace_payload(
        workspace_id="a" * 32,
        repository_url="https://github.com/example/private-repo",
        ref="main",
        git_token="ghs_workspace-only-token",
        expires_at=1_700_000_000,
    )
    push_payload = payloads.build_broker_push_payload(
        branch="inzozi-code/feature",
        expected_head="b" * 40,
        git_token="ghs_workspace-only-token",
    )
    for payload in (create_payload, push_payload):
        serialized = json.dumps(payload, sort_keys=True)
        for poison_key, poison_value in IDENTITY_POISON.items():
            assert poison_key not in payload
            assert poison_value not in serialized
        assert "AUTH_SESSION_SECRET" not in serialized
        assert "invitation_token" not in serialized
        assert SESSION_COOKIE not in serialized
        assert "password_hash" not in serialized


def test_git_helper_environment_does_not_forward_host_identity_secrets(monkeypatch, tmp_path):
    for key, value in IDENTITY_POISON.items():
        monkeypatch.setenv(key, value)

    git_credentials = _load_service_module(
        REPO_ROOT / "services/workspace/runtime/app",
        "inzozi_runtime_app_for_secret_tests",
        "git_credentials",
    )
    env = git_credentials.github_git_environment("ghs_workspace-only-token")
    for poison_key, poison_value in IDENTITY_POISON.items():
        assert poison_key not in env
        assert poison_value not in env.values()
    assert env["WORKSPACE_GIT_TOKEN"] == "ghs_workspace-only-token"
    assert "AUTH_SESSION_SECRET" not in env

    askpass = tmp_path / ".git-askpass.sh"
    askpass.write_text("#!/bin/sh\n", encoding="utf-8")
    auth_env = git_credentials._base_process_environment()
    auth_env.update(
        {
            "GIT_ASKPASS": str(askpass),
            "GIT_ASKPASS_REQUIRE": "force",
            "INZOZI_GIT_USERNAME": "x-access-token",
            "INZOZI_GIT_TOKEN": "ghs_workspace-only-token",
        }
    )
    for poison_key, poison_value in IDENTITY_POISON.items():
        assert poison_key not in auth_env
        assert poison_value not in auth_env.values()


def test_project_file_materialization_excludes_identity_secrets(tmp_path, monkeypatch):
    for key, value in IDENTITY_POISON.items():
        monkeypatch.setenv(key, value)
    metadata = {
        "repository_url": "https://github.com/example/private-repo",
        "ref": "main",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "bootstrap_authenticated": True,
    }
    target = tmp_path / "workspace.json"
    target.write_text(json.dumps(metadata), encoding="utf-8")
    written = json.loads(target.read_text(encoding="utf-8"))
    serialized = json.dumps(written, sort_keys=True)
    for poison_key, poison_value in IDENTITY_POISON.items():
        assert poison_key not in written
        assert poison_value not in serialized
    assert "password_hash" not in written
    assert "invitation_token" not in written
    assert SESSION_COOKIE not in serialized


def test_identity_public_and_audit_serialization_exclude_secrets():
    password_hash = hash_password("a-secure-staging-password", salt=b"z" * 16)
    user = AuthUser(
        user_id=str(uuid4()),
        email="dev@inzozidigital.com",
        organization_id="inzozi-digital",
        role="developer",
        status="active",
        session_version=0,
        created_by_user_id=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        activated_at=datetime.now(timezone.utc),
        disabled_at=None,
        password_hash=password_hash,
    )
    public = user.public_dict()
    assert "password_hash" not in public
    assert password_hash not in json.dumps(public)
    assert DUMMY_PASSWORD_HASH not in json.dumps(public)

    audit_details = {
        "event": "workspace.create",
        "outcome": "allowed",
        "request_id": "req-1",
        "actor_email": user.email,
        "actor_user_id": user.user_id,
    }
    serialized = json.dumps(audit_details, sort_keys=True)
    for poison_value in IDENTITY_POISON.values():
        assert poison_value not in serialized
    assert password_hash not in serialized
    assert SESSION_COOKIE not in serialized
