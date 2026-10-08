import json
from pathlib import Path

import pytest

from app import node_prepare


def make_catalog(tmp_path: Path, packages: dict[str, str]) -> Path:
    catalog = tmp_path / "catalog"
    (catalog / "node_modules").mkdir(parents=True)
    (catalog / "package.json").write_text(
        json.dumps({"dependencies": packages}), encoding="utf-8"
    )
    return catalog


def test_prepare_links_only_approved_exact_dependencies(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    (project / "package.json").write_text(
        json.dumps({"dependencies": {"react": "18.3.1"}}), encoding="utf-8"
    )
    catalog = make_catalog(tmp_path, {"react": "18.3.1"})
    monkeypatch.setattr(node_prepare, "CATALOG_ROOT", catalog)
    monkeypatch.setattr(node_prepare, "CATALOG_MODULES", catalog / "node_modules")

    result = node_prepare.prepare(project)

    assert result["status"] == "prepared"
    assert result["network_used"] is False
    assert (project / "node_modules").is_symlink()
    assert (project / "node_modules").resolve() == (catalog / "node_modules").resolve()


def test_prepare_rejects_dependency_outside_catalog(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    (project / "package.json").write_text(
        json.dumps({"dependencies": {"left-pad": "1.3.0"}}), encoding="utf-8"
    )
    catalog = make_catalog(tmp_path, {"react": "18.3.1"})
    monkeypatch.setattr(node_prepare, "CATALOG_ROOT", catalog)
    monkeypatch.setattr(node_prepare, "CATALOG_MODULES", catalog / "node_modules")

    with pytest.raises(RuntimeError, match="outside the approved"):
        node_prepare.prepare(project)
