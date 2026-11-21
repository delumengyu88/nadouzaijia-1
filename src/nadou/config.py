from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings. The database default matches docker-compose and is local only."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+psycopg://nadou:nadou_dev_password@localhost:5432/nadou"
    app_env: str = "local"


@lru_cache
def get_settings() -> Settings:
    return Settings()
