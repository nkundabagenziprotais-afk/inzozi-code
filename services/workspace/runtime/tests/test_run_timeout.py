from __future__ import annotations

import time
from pathlib import Path

from app.main import _run
from app.policy import recipe_for


def test_timeout_terminates_descendant_processes(tmp_path: Path) -> None:
    marker = tmp_path / "orphan.txt"
    result = _run(
        ("sh", "-c", "(sleep 1; printf orphan > orphan.txt) & wait"),
        tmp_path,
        0.1,
    )

    assert result["exit_code"] == 124
    assert result["timed_out"] is True
    time.sleep(1.2)
    assert not marker.exists()


def test_timeout_preserves_bounded_output(tmp_path: Path) -> None:
    result = _run(("sh", "-c", "printf ready; sleep 5"), tmp_path, 0.1)

    assert result["exit_code"] == 124
    assert result["timed_out"] is True
    assert "ready" in result["output"]


def test_node_build_has_bounded_five_minute_window() -> None:
    assert recipe_for("node_build").timeout_seconds == 300
