from app.agents.workspace_tools import allowed_actions_for_mode, tools_for_mode


def tool_names(mode: str) -> set[str]:
    return {tool.name for tool in tools_for_mode(mode, has_workspace=True)}


def test_read_only_modes_do_not_receive_write_or_command_tools() -> None:
    for mode in ("ask", "plan", "review", "deploy"):
        names = tool_names(mode)
        assert names == {
            "search_repository",
            "read_repository_file",
            "inspect_git_status",
            "inspect_git_diff",
        }
        assert allowed_actions_for_mode(mode) == frozenset()


def test_build_and_debug_receive_only_guarded_mutation_tools() -> None:
    for mode in ("build", "debug"):
        names = tool_names(mode)
        assert "create_checkpoint" in names
        assert "write_repository_file" in names
        assert "run_guarded_action" in names
        assert "run_shell" not in names
        assert "execute_command" not in names
        assert "rm" not in names
        assert allowed_actions_for_mode(mode) == frozenset({
            "python_compile",
            "python_tests",
            "node_build",
            "node_test",
            "node_lint",
            "php_tests",
            "composer_validate",
        })


def test_no_workspace_means_no_repository_tools() -> None:
    assert tools_for_mode("build", has_workspace=False) == []
