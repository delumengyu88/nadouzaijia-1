from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings. The database default matches docker-compose and is local only."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+psycopg://nadou:nadou_dev_password@localhost:5432/nadou"
    app_env: str = "local"
    allow_dev_seed: bool = Field(
        default=False,
        validation_alias=AliasChoices("NADOU_ALLOW_DEV_SEED"),
    )
    dingtalk_webhook_url: str = Field(
        default="",
        validation_alias=AliasChoices("NADOU_DINGTALK_WEBHOOK_URL"),
    )
    dingtalk_webhook_secret: str = Field(
        default="",
        validation_alias=AliasChoices("NADOU_DINGTALK_WEBHOOK_SECRET"),
    )
    task_export_path: str = Field(
        default="",
        validation_alias=AliasChoices("NADOU_TASK_EXPORT_PATH"),
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
