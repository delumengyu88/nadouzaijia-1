from functools import lru_cache

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings. The database default matches docker-compose and is local only."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+psycopg://nadou:nadou_dev_password@localhost:5432/nadou"
    app_env: str = "local"
    stop_words_path: str = Field(default="", validation_alias="NADOU_STOP_WORDS_PATH")
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
    wx_appid: str = Field(default="", validation_alias="NADOU_WX_APPID")
    wx_appsecret: SecretStr = Field(default=SecretStr(""), validation_alias="NADOU_WX_APPSECRET")
    data_source: str = Field(
        default="demo",
        validation_alias=AliasChoices("NADOU_DATA_SOURCE"),
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
