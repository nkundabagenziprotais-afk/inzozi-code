from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

IDENTITY_MODES = frozenset({"bootstrap", "database"})


class Settings(BaseSettings):
    app_env: str = "development"
    app_name: str = "Inzozi Code"
    database_url: str = "postgresql://inzozi_code:change-me@postgres:5432/inzozi_code"
    redis_url: str = "redis://redis:6379/0"
    redis_socket_connect_timeout_seconds: float = 1.0
    redis_socket_timeout_seconds: float = 1.0
    aquila_provider: str = "mock"
    aquila_model: str = "mock-v0"
    workspace_service_url: str = "http://workspace:8100"
    workspace_ownership_enforced: bool = False
    github_app_id: str = ""
    github_app_owner: str = ""
    github_app_default_installation_id: str = ""
    github_app_private_key: str = ""
    github_app_private_key_path: str = ""

    auth_enabled: bool = False
    # bootstrap = single env bootstrap identity; database = durable PostgreSQL users.
    auth_identity_mode: str = "bootstrap"
    auth_bootstrap_email: str = ""
    auth_password_hash: str = ""
    auth_session_secret: str = ""
    auth_bootstrap_role: str = "platform_owner"
    auth_organization_id: str = "inzozi-digital"
    auth_session_ttl_minutes: int = 480
    auth_cookie_secure: bool = False
    # Comma-separated proxy hostnames, IPs, or CIDRs allowed to supply X-Real-IP.
    # Empty means forwarding headers are never trusted (peer IP only).
    auth_trusted_proxies: str = ""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @field_validator("auth_identity_mode")
    @classmethod
    def _validate_identity_mode(cls, value: str) -> str:
        normalized = (value or "").strip().casefold()
        if normalized not in IDENTITY_MODES:
            raise ValueError("AUTH_IDENTITY_MODE must be bootstrap or database")
        return normalized


@lru_cache
def get_settings() -> Settings:
    return Settings()
