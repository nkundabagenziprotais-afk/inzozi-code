import json
from pathlib import Path

import pytest

from app import node_prepare


def make_catalog(tmp_path: Path, packages: dict[str, str]) -> Path:
    catalog = tmp_path / "catalog"
    modules = catalog / "node_modules"
    modules.mkdir(parents=True)
    for name in packages:
        package_path = modules / name
        package_path.mkdir(parents=True)
        (package_path / "package.json").write_text(
            json.dumps({"name": name, "version": packages[name]}), encoding="utf-8"
        )
    bins = modules / ".bin"
    bins.mkdir()
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
    modules = project / "node_modules"
    assert modules.is_dir()
    assert not modules.is_symlink()
    assert (modules / "react").is_symlink()
    assert (modules / "react").resolve() == (catalog / "node_modules" / "react").resolve()
    assert (modules / ".bin").is_symlink()
    assert (modules / ".inzozi-catalog.json").is_file()
    # Tool caches can be created locally without trying to write to /opt.
    (modules / ".vite").mkdir()


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


def test_prepare_is_idempotent_for_runtime_managed_node_modules(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    (project / "package.json").write_text(
        json.dumps({"dependencies": {"react": "18.3.1"}}), encoding="utf-8"
    )
    catalog = make_catalog(tmp_path, {"react": "18.3.1"})
    monkeypatch.setattr(node_prepare, "CATALOG_ROOT", catalog)
    monkeypatch.setattr(node_prepare, "CATALOG_MODULES", catalog / "node_modules")

    node_prepare.prepare(project)
    second = node_prepare.prepare(project)

    assert second["status"] == "prepared"
    assert (project / "node_modules" / "react").is_symlink()


def test_prepare_rejects_unmanaged_existing_node_modules(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    (project / "package.json").write_text(
        json.dumps({"dependencies": {"react": "18.3.1"}}), encoding="utf-8"
    )
    (project / "node_modules").mkdir()
    catalog = make_catalog(tmp_path, {"react": "18.3.1"})
    monkeypatch.setattr(node_prepare, "CATALOG_ROOT", catalog)
    monkeypatch.setattr(node_prepare, "CATALOG_MODULES", catalog / "node_modules")

    with pytest.raises(RuntimeError, match="was not prepared"):
        node_prepare.prepare(project)
