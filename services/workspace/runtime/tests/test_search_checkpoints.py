from pathlib import Path

from app import main


def make_workspace(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(main, "WORKSPACE_ROOT", tmp_path)
    workspace_id = "a" * 32
    repo = tmp_path / workspace_id / "repo"
    repo.mkdir(parents=True)
    (repo / ".git").mkdir()
    return workspace_id, repo


def test_search_skips_generated_directories(tmp_path, monkeypatch):
    workspace_id, repo = make_workspace(tmp_path, monkeypatch)
    (repo / "src").mkdir()
    (repo / "src" / "service.py").write_text("before\nneedle is here\nafter\n", encoding="utf-8")
    (repo / "node_modules").mkdir()
    (repo / "node_modules" / "ignored.js").write_text("needle should not be indexed\n", encoding="utf-8")

    result = main.search_workspace(workspace_id, "needle")

    assert len(result["results"]) == 1
    assert result["results"][0]["path"] == "src/service.py"
    assert result["results"][0]["line"] == 2


def test_restore_snapshot_preserves_git_metadata(tmp_path):
    repo = tmp_path / "repo"
    snapshot = tmp_path / "snapshot"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / ".git" / "config").write_text("keep-git-metadata", encoding="utf-8")
    (repo / "app.py").write_text("original", encoding="utf-8")
    (repo / "nested").mkdir()
    (repo / "nested" / "value.txt").write_text("one", encoding="utf-8")

    main._copy_snapshot(repo, snapshot)

    (repo / "app.py").write_text("changed", encoding="utf-8")
    (repo / "nested" / "value.txt").unlink()
    (repo / "temporary.txt").write_text("remove-me", encoding="utf-8")

    main._restore_snapshot(snapshot, repo)

    assert (repo / "app.py").read_text(encoding="utf-8") == "original"
    assert (repo / "nested" / "value.txt").read_text(encoding="utf-8") == "one"
    assert not (repo / "temporary.txt").exists()
    assert (repo / ".git" / "config").read_text(encoding="utf-8") == "keep-git-metadata"
