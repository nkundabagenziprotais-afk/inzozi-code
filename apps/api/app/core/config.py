from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    app_name: str = "Inzozi Code"
    database_url: str = "postgresql://inzozi_code:change-me@postgres:5432/inzozi_code"
    redis_url: str = "redis://redis:6379/0"
    aquila_provider: str = "mock"
    aquila_model: str = "mock-v0"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
