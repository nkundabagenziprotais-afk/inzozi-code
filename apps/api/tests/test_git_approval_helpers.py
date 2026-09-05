from datetime import datetime, timedelta, timezone

from app.routes.workspace import (
    COMMIT_APPROVALS,
    PUBLIC_ACTIONS,
    SAFE_BRANCH_RE,
    _changed_paths,
    _clean_approvals,
    _fingerprint,
    _secret_bearing_path,
)


def test_changed_paths_ignores_branch_header_and_handles_rename() -> None:
    status = "## feature/test\n M app/main.py\n?? docs/new.md\nR  old.py -> new.py\n"
    assert _changed_paths(status) == ["app/main.py", "docs/new.md", "new.py"]


def test_sensitive_paths_are_blocked_but_env_example_is_allowed() -> None:
    blocked = (
        ".env",
        ".env.local",
        "config/private.pem",
        "secrets/api.key",
        "keys/id_ed25519",
        "service-account.json",
    )
    for path in blocked:
        assert _secret_bearing_path(path) is True
    assert _secret_bearing_path(".env.example") is False
    assert _secret_bearing_path("src/keyboard.keymap.ts") is False


def test_review_fingerprint_changes_with_branch_head_status_or_diff() -> None:
    baseline = _fingerprint("feature/a", "abc", " M a.py", "diff-a")
    assert _fingerprint("feature/b", "abc", " M a.py", "diff-a") != baseline
    assert _fingerprint("feature/a", "def", " M a.py", "diff-a") != baseline
    assert _fingerprint("feature/a", "abc", " M b.py", "diff-a") != baseline
    assert _fingerprint("feature/a", "abc", " M a.py", "diff-b") != baseline


def test_safe_branch_policy_matches_project_conventions() -> None:
    for branch in (
        "feature/invoice-module",
        "fix/login-loop",
        "ui/admin-redesign",
        "hotfix/payment-error",
        "deploy/staging-release",
    ):
        assert SAFE_BRANCH_RE.fullmatch(branch)
    assert not SAFE_BRANCH_RE.fullmatch("main")
    assert not SAFE_BRANCH_RE.fullmatch("feature/UPPER")


def test_internal_commit_actions_are_not_public_generic_actions() -> None:
    for action in ("git_intent_add", "git_stage_all", "git_diff_review", "git_diff_check", "git_head"):
        assert action not in PUBLIC_ACTIONS


def test_expired_approval_is_removed() -> None:
    approval_id = "expired-test"
    COMMIT_APPROVALS[approval_id] = {
        "expires_at": datetime.now(timezone.utc) - timedelta(seconds=1),
    }
    _clean_approvals()
    assert approval_id not in COMMIT_APPROVALS
