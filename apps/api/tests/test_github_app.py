import pytest

from app.core.config import get_settings
from app.integrations.github_app import (
    GitHubAppError,
    default_installation_for,
    repository_coordinates_from_url,
)


def _configure(monkeypatch):
    monkeypatch.setenv("GITHUB_APP_ID", "123")
    monkeypatch.setenv("GITHUB_APP_OWNER", "nkundabagenziprotais-afk")
    monkeypatch.setenv("GITHUB_APP_DEFAULT_INSTALLATION_ID", "456")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", "test-key-not-used-for-signing")
    get_settings.cache_clear()


def test_repository_coordinates_accept_clean_github_https_url():
    assert repository_coordinates_from_url("https://github.com/example/private-repo.git") == ("example", "private-repo")


def test_repository_coordinates_reject_non_github_url():
    with pytest.raises(GitHubAppError):
        repository_coordinates_from_url("https://example.com/owner/repo")


def test_default_installation_only_applies_to_configured_owner(monkeypatch):
    _configure(monkeypatch)
    assert default_installation_for("https://github.com/nkundabagenziprotais-afk/inzozi-code") == 456
    assert default_installation_for("https://github.com/other-owner/public-repo") is None
    get_settings.cache_clear()
