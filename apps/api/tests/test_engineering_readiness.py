from __future__ import annotations

from app.core.config import get_settings
from app.routes import engineering_readiness


def test_engineering_readiness_reports_runtime_flags(monkeypatch):
    monkeypatch.setenv("WORKSPACE_OWNERSHIP_ENFORCED", "false")
    get_settings.cache_clear()
    monkeypatch.setattr(engineering_readiness, "github_app_configured", lambda: True)

    assert engineering_readiness.engineering_readiness() == {
        "github_app_configured": True,
        "workspace_ownership_enforced": False,
    }

    monkeypatch.setenv("WORKSPACE_OWNERSHIP_ENFORCED", "true")
    get_settings.cache_clear()

    assert engineering_readiness.engineering_readiness() == {
        "github_app_configured": True,
        "workspace_ownership_enforced": True,
    }

    get_settings.cache_clear()
