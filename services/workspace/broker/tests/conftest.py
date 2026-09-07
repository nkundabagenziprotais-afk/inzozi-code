from __future__ import annotations

from unittest.mock import MagicMock

import docker


# Broker production code intentionally initializes its Docker client when the
# module is imported. Unit tests validate broker policy and argument handling;
# they must not require a developer workstation to have a running Docker
# daemon. Patch docker.from_env before pytest imports app.main.
docker.from_env = lambda *args, **kwargs: MagicMock(
    name="broker_test_docker_client"
)
