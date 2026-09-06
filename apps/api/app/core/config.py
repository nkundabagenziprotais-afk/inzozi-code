from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    app_name: str = "Inzozi Code"
    database_url: str = "postgresql://inzozi_code:change-me@postgres:5432/inzozi_code"
    redis_url: str = "redis://redis:6379/0"
    aquila_provider: str = "mock"
    aquila_model: str = "mock-v0"
    workspace_service_url: str = "http://workspace:8100"
    github_app_id: str = ""
    github_app_owner: str = ""
    github_app_default_installation_id: str = ""
    github_app_private_key: str = ""
    github_app_private_key_path: str = ""

    auth_enabled: bool = False
    auth_bootstrap_email: str = ""
    auth_password_hash: str = ""
    auth_session_secret: str = ""
    auth_bootstrap_role: str = "platform_owner"
    auth_organization_id: str = "inzozi-digital"
    auth_session_ttl_minutes: int = 480
    auth_cookie_secure: bool = False

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
