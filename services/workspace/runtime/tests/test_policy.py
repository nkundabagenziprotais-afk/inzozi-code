from pathlib import Path

import pytest

from app.policy import PolicyError, recipe_for, resolve_inside


def test_known_recipe_is_resolved() -> None:
    recipe = recipe_for("git_status")
    assert recipe.argv[:2] == ("git", "status")


def test_arbitrary_shell_action_is_rejected() -> None:
    with pytest.raises(PolicyError):
        recipe_for("rm -rf /")


def test_path_escape_is_rejected(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with pytest.raises(PolicyError):
        resolve_inside(workspace, "../outside")


def test_node_prepare_uses_runtime_absolute_script_path() -> None:
    recipe = recipe_for("node_prepare")
    assert recipe.argv == ("python", "/runtime/app/node_prepare.py")
    assert "sh" not in recipe.argv
    assert "bash" not in recipe.argv
