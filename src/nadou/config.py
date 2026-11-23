import ipaddress
from functools import lru_cache

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class TrustedProxies:
    """Comma-separated proxy IPs and CIDRs. An empty value trusts no proxy."""

    @staticmethod
    def parse(value: str) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
        networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
        for part in value.split(","):
            token = part.strip()
            if not token:
                continue
            try:
                if "/" in token:
                    network = ipaddress.ip_network(token, strict=True)
                else:
                    address = ipaddress.ip_address(token)
                    network = ipaddress.ip_network(f"{address}/{address.max_prefixlen}")
            except ValueError as exc:
                raise ValueError(
                    "NADOU_TRUSTED_PROXIES entries must be IP addresses or CIDR networks"
                ) from exc
            networks.append(network)
        return tuple(networks)


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
    xpay_env: int | None = Field(default=None, ge=0, le=1, validation_alias="NADOU_XPAY_ENV")
    xpay_offer: str = Field(default="", validation_alias="NADOU_XPAY_OFFER_ID")
    xpay_live_key: SecretStr = Field(default=SecretStr(""), validation_alias="NADOU_XPAY_LIVE_KEY")
    xpay_sandbox_key: SecretStr = Field(
        default=SecretStr(""), validation_alias="NADOU_XPAY_SANDBOX_KEY"
    )
    xpay_products: dict[str, str] = Field(
        default_factory=dict, validation_alias="NADOU_XPAY_PRODUCTS"
    )
    # Enable sales only after the existing receiver's authenticated refund route is verified.
    diamond_refund_route_verified: bool = Field(
        default=False, validation_alias="NADOU_XPAY_DIAMOND_REFUND_ROUTE_VERIFIED"
    )
    xpay_push_token: SecretStr = Field(
        default=SecretStr(""), validation_alias="NADOU_XPAY_PUSH_TOKEN"
    )
    xpay_push_aes: SecretStr = Field(default=SecretStr(""), validation_alias="NADOU_XPAY_PUSH_AES")
    wx_appid: str = Field(default="", validation_alias="NADOU_WX_APPID")
    wx_appsecret: SecretStr = Field(default=SecretStr(""), validation_alias="NADOU_WX_APPSECRET")
    wechat_mch_id: str = Field(default="", validation_alias="NADOU_WECHAT_MCH_ID")
    wechat_api_v3_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias="NADOU_WECHAT_API_V3_KEY",
    )
    wechat_mch_private_key_path: str = Field(
        default="",
        validation_alias="NADOU_WECHAT_MCH_PRIVATE_KEY_PATH",
    )
    wechat_mch_cert_serial: str = Field(
        default="",
        validation_alias="NADOU_WECHAT_MCH_CERT_SERIAL",
    )
    wechat_pay_public_key_path: str = Field(
        default="",
        validation_alias="NADOU_WECHAT_PAY_PUBLIC_KEY_PATH",
    )
    wechat_pay_public_key_id: str = Field(
        default="",
        validation_alias="NADOU_WECHAT_PAY_PUBLIC_KEY_ID",
    )
    wechat_notify_url: str = Field(default="", validation_alias="NADOU_WECHAT_NOTIFY_URL")
    wechat_pay_test_tier: bool = Field(
        default=False,
        validation_alias="NADOU_WECHAT_PAY_TEST_TIER",
    )
    wechat_pay_test_users: str = Field(
        default="",
        validation_alias="NADOU_WECHAT_PAY_TEST_USERS",
    )
    data_source: str = Field(
        default="unverified",
        validation_alias=AliasChoices("NADOU_DATA_SOURCE"),
    )
    trusted_proxies: str = Field(
        default="127.0.0.1",
        validation_alias=AliasChoices("NADOU_TRUSTED_PROXIES"),
    )
    # Experience-build switch. Only the exact value "1" enables it. Production stays "0".
    # Delete this field with src/nadou/devtools before review.
    trial_tools: str = Field(default="0", validation_alias="NADOU_TRIAL_TOOLS")

    @property
    def demo_data(self) -> bool:
        return self.app_env in {"local", "test"} and self.data_source == "demo"

    @field_validator("trusted_proxies")
    @classmethod
    def _trusted_proxies_are_networks(cls, value: str) -> str:
        TrustedProxies.parse(value)
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
