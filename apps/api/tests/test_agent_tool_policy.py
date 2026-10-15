import pytest

from app.agents.workspace_tools import allowed_actions_for_mode, branch_action_for, tools_for_mode


def tool_names(mode: str) -> set[str]:
    return {tool.name for tool in tools_for_mode(mode, has_workspace=True)}


def test_read_only_modes_do_not_receive_write_or_command_tools() -> None:
    for mode in ("ask", "plan", "design", "review", "deploy"):
        names = tool_names(mode)
        assert names == {
            "list_repository_tree",
            "search_repository",
            "read_repository_file",
            "inspect_git_status",
            "inspect_git_diff",
        }
        assert allowed_actions_for_mode(mode) == frozenset()


def test_build_and_debug_receive_only_guarded_mutation_tools() -> None:
    for mode in ("build", "debug"):
        names = tool_names(mode)
        assert "list_repository_tree" in names
        assert "create_feature_branch" in names
        assert "create_checkpoint" in names
        assert "write_repository_file" in names
        assert "run_guarded_action" in names
        assert "create_feature_branch" in names
        assert "run_shell" not in names
        assert "execute_command" not in names
        assert "rm" not in names
        assert allowed_actions_for_mode(mode) == frozenset({
            "python_compile",
            "python_tests",
            "node_prepare",
            "node_build",
            "node_test",
            "node_lint",
            "php_tests",
            "composer_validate",
        })


def test_no_workspace_means_no_repository_tools() -> None:
    assert tools_for_mode("build", has_workspace=False) == []


def test_branch_action_uses_guarded_runtime_recipe() -> None:
    assert branch_action_for(" feature/audio-capture-mvp ") == "git_create_branch:feature/audio-capture-mvp"


@pytest.mark.parametrize(
    "branch_name",
    ["main", "Feature/name", "feature/a", "feature/name with space", "feature/../../bad"],
)
def test_branch_action_rejects_unsafe_names(branch_name: str) -> None:
    with pytest.raises(ValueError):
        branch_action_for(branch_name)
