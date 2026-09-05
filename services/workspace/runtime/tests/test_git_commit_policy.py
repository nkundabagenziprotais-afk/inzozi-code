import base64

import pytest

from app.policy import PolicyError, recipe_for


def encode(message: str) -> str:
    return base64.urlsafe_b64encode(message.encode("utf-8")).decode("ascii").rstrip("=")


def test_safe_feature_branch_recipe_is_explicit() -> None:
    recipe = recipe_for("git_create_branch:feature/invoice-review")
    assert recipe.argv == ("git", "switch", "-c", "feature/invoice-review")


def test_protected_or_unsafe_branch_names_are_rejected() -> None:
    for action in (
        "git_create_branch:main",
        "git_create_branch:Feature/Uppercase",
        "git_create_branch:../../escape",
        "git_create_branch:feature/x",
    ):
        with pytest.raises(PolicyError):
            recipe_for(action)


def test_commit_message_is_decoded_without_shell_execution() -> None:
    message = "feat: add approval gate"
    recipe = recipe_for(f"git_commit_b64:{encode(message)}")
    assert recipe.argv[-3:] == ("commit", "-m", message)
    assert recipe.argv[0] == "git"
    assert "sh" not in recipe.argv
    assert "bash" not in recipe.argv


def test_multiline_commit_message_is_rejected() -> None:
    with pytest.raises(PolicyError):
        recipe_for(f"git_commit_b64:{encode('feat: line one\nline two')}")


def test_internal_review_recipes_do_not_invoke_shell() -> None:
    for action in (
        "git_intent_add",
        "git_stage_all",
        "git_write_tree",
        "git_unstage_all",
        "git_diff_review",
        "git_diff_check",
        "git_head",
    ):
        recipe = recipe_for(action)
        assert recipe.argv[0] == "git"
        assert "sh" not in recipe.argv
        assert "bash" not in recipe.argv


def test_unstage_recipe_does_not_discard_working_tree_changes() -> None:
    assert recipe_for("git_unstage_all").argv == ("git", "reset", "--mixed", "HEAD")
