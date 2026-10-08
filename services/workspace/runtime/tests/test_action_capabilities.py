from pathlib import Path

from app import main


def test_missing_executable_returns_structured_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(main.subprocess, "run", lambda *args, **kwargs: (_ for _ in ()).throw(FileNotFoundError()))
    result = main._run(("missing-tool", "--version"), tmp_path, 5)
    assert result["exit_code"] == 127
    assert result["unavailable"] is True
    assert "missing-tool" in result["output"]


def test_node_build_capability_requires_prepared_dependencies(tmp_path, monkeypatch):
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(main.shutil, "which", lambda name: f"/usr/bin/{name}")
    capability = main._action_capability("node_build", tmp_path)
    assert capability["available"] is False
    assert "node_prepare" in capability["reason"]


def test_node_prepare_capability_requires_package_json(tmp_path, monkeypatch):
    monkeypatch.setattr(main.shutil, "which", lambda name: f"/usr/bin/{name}")
    capability = main._action_capability("node_prepare", tmp_path)
    assert capability["available"] is False
    assert "package.json" in capability["reason"]
