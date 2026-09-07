from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path
import re


class PolicyError(ValueError):
    pass


@dataclass(frozen=True)
class CommandRecipe:
    argv: tuple[str, ...]
    timeout_seconds: int = 120


RECIPES: dict[str, CommandRecipe] = {
    "git_status": CommandRecipe(("git", "status", "--short", "--branch"), 30),
    "git_diff": CommandRecipe(("git", "diff", "--no-ext-diff", "--minimal"), 30),
    "git_diff_review": CommandRecipe(("git", "diff", "--binary", "--no-ext-diff", "--minimal"), 30),
    "git_diff_check": CommandRecipe(("git", "diff", "--check"), 30),
    "git_branch": CommandRecipe(("git", "branch", "--show-current"), 30),
    "git_head": CommandRecipe(("git", "rev-parse", "HEAD"), 30),
    "git_log": CommandRecipe(("git", "log", "-n", "20", "--oneline", "--decorate"), 30),
    "git_intent_add": CommandRecipe(("git", "add", "-N", "."), 30),
    "git_stage_all": CommandRecipe(("git", "add", "-A"), 30),
    "git_write_tree": CommandRecipe(("git", "write-tree"), 30),
    "git_unstage_all": CommandRecipe(("git", "reset", "--mixed", "HEAD"), 30),
    "python_compile": CommandRecipe(("python", "-m", "compileall", "."), 120),
    "python_tests": CommandRecipe(("pytest", "-q"), 180),
    "node_build": CommandRecipe(("npm", "run", "build"), 180),
    "node_test": CommandRecipe(("npm", "test", "--", "--runInBand"), 180),
    "node_lint": CommandRecipe(("npm", "run", "lint"), 180),
    "php_tests": CommandRecipe(("php", "artisan", "test"), 180),
    "composer_validate": CommandRecipe(("composer", "validate", "--no-check-publish"), 120),
}

_SAFE_BRANCH_RE = re.compile(r"^(feature|fix|ui|hotfix|deploy)/[a-z0-9][a-z0-9._-]{2,80}$")
_COMMIT_PREFIX = "git_commit_b64:"
_BRANCH_PREFIX = "git_create_branch:"


def _dynamic_recipe(action: str) -> CommandRecipe | None:
    if action.startswith(_BRANCH_PREFIX):
        branch = action[len(_BRANCH_PREFIX):]
        if not _SAFE_BRANCH_RE.fullmatch(branch):
            raise PolicyError("Branch name must use feature/, fix/, ui/, hotfix/, or deploy/ with a safe lowercase slug")
        return CommandRecipe(("git", "switch", "-c", branch), 30)

    if action.startswith(_COMMIT_PREFIX):
        encoded = action[len(_COMMIT_PREFIX):]
        try:
            padding = "=" * (-len(encoded) % 4)
            message = base64.urlsafe_b64decode((encoded + padding).encode("ascii")).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise PolicyError("Invalid encoded commit message") from exc
        if not 5 <= len(message) <= 120 or "\n" in message or "\r" in message:
            raise PolicyError("Commit message must be 5 to 120 characters on one line")
        return CommandRecipe((
            "git",
            "-c", "user.name=Inzozi Code",
            "-c", "user.email=noreply@inzozidigital.com",
            "commit", "-m", message,
        ), 60)

    return None


def recipe_for(action: str) -> CommandRecipe:
    recipe = RECIPES.get(action)
    if recipe is not None:
        return recipe
    dynamic = _dynamic_recipe(action)
    if dynamic is not None:
        return dynamic
    raise PolicyError(f"Unsupported workspace action: {action}")


def resolve_inside(root: Path, relative: str | None = None) -> Path:
    root = root.resolve()
    target = root if not relative else (root / relative).resolve()
    if target != root and root not in target.parents:
        raise PolicyError("Path escapes the workspace boundary")
    return target
