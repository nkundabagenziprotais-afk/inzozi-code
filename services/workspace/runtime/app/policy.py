from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class PolicyError(ValueError):
    pass


@dataclass(frozen=True)
class CommandRecipe:
    argv: tuple[str, ...]
    timeout_seconds: int = 120


RECIPES: dict[str, CommandRecipe] = {
    "git_status": CommandRecipe(("git", "status", "--short", "--branch"), 30),
    "git_diff": CommandRecipe(("git", "diff", "--no-ext-diff", "--minimal"), 30),
    "git_branch": CommandRecipe(("git", "branch", "--show-current"), 30),
    "git_log": CommandRecipe(("git", "log", "-n", "20", "--oneline", "--decorate"), 30),
    "python_compile": CommandRecipe(("python", "-m", "compileall", "."), 120),
    "python_tests": CommandRecipe(("pytest", "-q"), 180),
    "node_build": CommandRecipe(("npm", "run", "build"), 180),
    "node_test": CommandRecipe(("npm", "test", "--", "--runInBand"), 180),
    "node_lint": CommandRecipe(("npm", "run", "lint"), 180),
    "php_tests": CommandRecipe(("php", "artisan", "test"), 180),
    "composer_validate": CommandRecipe(("composer", "validate", "--no-check-publish"), 120),
}


def recipe_for(action: str) -> CommandRecipe:
    try:
        return RECIPES[action]
    except KeyError as exc:
        raise PolicyError(f"Unsupported workspace action: {action}") from exc


def resolve_inside(root: Path, relative: str | None = None) -> Path:
    root = root.resolve()
    target = root if not relative else (root / relative).resolve()
    if target != root and root not in target.parents:
        raise PolicyError("Path escapes the workspace boundary")
    return target
