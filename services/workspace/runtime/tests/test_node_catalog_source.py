from pathlib import Path


EXPECTED_NOTEBOOK_PACKAGES = (
    "@tiptap/core@3.31.4",
    "@tiptap/react@3.31.4",
    "@tiptap/pm@3.31.4",
    "@tiptap/starter-kit@3.31.4",
    "@tiptap/extension-link@3.31.4",
    "dexie@4.4.6",
    "fake-indexeddb@6.2.5",
    "uuid@14.0.2",
)


def test_notebook_dependencies_are_pinned_in_guarded_node_catalog() -> None:
    dockerfile = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text(encoding="utf-8")
    for package in EXPECTED_NOTEBOOK_PACKAGES:
        assert package in dockerfile


def test_guarded_catalog_still_uses_exact_install_without_runtime_network() -> None:
    dockerfile = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text(encoding="utf-8")
    assert "npm install --save-exact --no-audit --no-fund" in dockerfile
    assert "npm install" in dockerfile
