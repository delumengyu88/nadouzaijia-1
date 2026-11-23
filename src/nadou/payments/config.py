"""Load WeChat Pay settings from the environment. Incomplete config is not an error."""

import logging
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.types import PublicKeyTypes
from fastapi import HTTPException

from nadou.config import Settings, get_settings
from nadou.payments import xpay, xpay_push
from nadou.payments.catalog import DIAMOND, LIVE_TEST
from nadou.payments.crypto import load_private_key, load_public_key

logger = logging.getLogger("nadou.payments")


@dataclass(frozen=True)
class WeChatPaySettings:
    appid: str
    mchid: str
    api_v3_key: str = field(repr=False)
    private_key: rsa.RSAPrivateKey = field(repr=False)
    mch_serial: str
    platform_key: PublicKeyTypes = field(repr=False)
    platform_key_id: str
    notify_url: str


def _read(path: str) -> bytes | None:
    if not path:
        return None
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    if not data.strip():
        return None
    return data


def load_pay_settings(settings: Settings) -> WeChatPaySettings | None:
    """Return None when any required value is missing. Never raises."""
    try:
        return _load(settings)
    except Exception:
        logger.warning("wechat pay config is incomplete")
        return None


def _load(settings: Settings) -> WeChatPaySettings | None:
    appid = settings.wx_appid.strip()
    mchid = settings.wechat_mch_id.strip()
    api_v3_key = settings.wechat_api_v3_key.get_secret_value().strip()
    serial = settings.wechat_mch_cert_serial.strip()
    platform_id = settings.wechat_pay_public_key_id.strip()
    notify_url = settings.wechat_notify_url.strip()
    private_pem = _read(settings.wechat_mch_private_key_path.strip())
    public_pem = _read(settings.wechat_pay_public_key_path.strip())
    if not all((appid, mchid, serial, platform_id, notify_url, private_pem, public_pem)):
        return None
    if len(api_v3_key.encode()) != 32 or not notify_url.startswith("https://"):
        return None
    private_key = load_private_key(private_pem or b"")
    platform_key = load_public_key(public_pem or b"")
    if not isinstance(platform_key, rsa.RSAPublicKey):
        return None
    return WeChatPaySettings(
        appid=appid,
        mchid=mchid,
        api_v3_key=api_v3_key,
        private_key=private_key,
        mch_serial=serial,
        platform_key=platform_key,
        platform_key_id=platform_id,
        notify_url=notify_url,
    )


def user_may_use_test_tier(user_id: str) -> bool:
    """Whether this user may see or buy any test SKU.

    membership_test and later products such as membership_dev share this gate:
    the switch must be on, and the user id must appear in NADOU_WECHAT_PAY_TEST_USERS.
    An empty list allows nobody.
    """
    settings = get_settings()
    if not settings.wechat_pay_test_tier:
        return False
    allowed = {
        part.strip()
        for part in settings.wechat_pay_test_users.replace("\n", ",").split(",")
        if part.strip()
    }
    return user_id in allowed


def live_test_product_id(user_id: str) -> str | None:
    settings = get_settings()
    if settings.xpay_env != 0 or not user_may_use_test_tier(user_id):
        return None
    if not settings.wx_appid or not settings.wx_appsecret.get_secret_value():
        return None
    try:
        xpay.configured()
        xpay_push.configured_key()
    except HTTPException:
        return None
    return _unique_product_id(LIVE_TEST.code)


def diamond_product_id() -> str | None:
    settings = get_settings()
    if (
        not settings.diamond_refund_route_verified
        or settings.xpay_env != 0
        or not settings.wx_appid
        or not settings.wx_appsecret.get_secret_value()
    ):
        return None
    try:
        xpay.configured()
        xpay_push.configured_key()
    except HTTPException:
        return None
    return _unique_product_id(DIAMOND.code)


def _unique_product_id(sku: str) -> str | None:
    products = get_settings().xpay_products
    product_id = products.get(sku)
    if not product_id or product_id != product_id.strip() or len(product_id) > 128:
        return None
    if any(code != sku and value == product_id for code, value in products.items()):
        return None
    return product_id
