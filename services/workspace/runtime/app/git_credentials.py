from __future__ import annotations

import os

GIT_ASKPASS_PATH = "/runtime/app/git_askpass.sh"


def github_git_environment(token: str) -> dict[str, str]:
    """Build Git HTTPS auth environment without creating executable files in /tmp."""
    if not token:
        raise ValueError("GitHub installation token is required")
    env = os.environ.copy()
    env.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ASKPASS_REQUIRE": "force",
            "GIT_ASKPASS": GIT_ASKPASS_PATH,
            "HOME": "/tmp",
            "WORKSPACE_GIT_TOKEN": token,
        }
    )
    return env
