from __future__ import annotations

import os

GIT_ASKPASS_PATH = "/runtime/app/git_askpass.sh"

_PROXY_ENV_KEYS = (
    "HTTPS_PROXY",
    "https_proxy",
    "HTTP_PROXY",
    "http_proxy",
    "NO_PROXY",
    "no_proxy",
)


def _base_process_environment() -> dict[str, str]:
    """Minimal process env for Git helpers — never copies host identity secrets."""
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": "/tmp",
        "GIT_TERMINAL_PROMPT": "0",
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
    }

    # Workspace Git helpers run on an internal-only Docker network.
    # The broker supplies the fixed GitHub egress proxy, so preserve only
    # those explicit proxy variables rather than copying container secrets.
    for key in _PROXY_ENV_KEYS:
        value = os.environ.get(key)
        if value is not None:
            env[key] = value

    return env


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
