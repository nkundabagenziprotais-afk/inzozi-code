from __future__ import annotations

import os

GIT_ASKPASS_PATH = "/runtime/app/git_askpass.sh"


def _base_process_environment() -> dict[str, str]:
    """Minimal process env for Git helpers — never copies host identity secrets."""
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": "/tmp",
        "GIT_TERMINAL_PROMPT": "0",
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
    }


def github_git_environment(token: str) -> dict[str, str]:
    """Build Git HTTPS auth environment without creating executable files in /tmp."""
    if not token:
        raise ValueError("GitHub installation token is required")
    env = _base_process_environment()
    env.update(
        {
            "GIT_ASKPASS_REQUIRE": "force",
            "GIT_ASKPASS": GIT_ASKPASS_PATH,
            "WORKSPACE_GIT_TOKEN": token,
        }
    )
    return env
