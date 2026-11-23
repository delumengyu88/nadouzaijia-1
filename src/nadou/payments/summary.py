"""Membership checkout opened from a display-only program summary."""

from datetime import datetime

from fastapi import HTTPException
from sqlalchemy.orm import Session

from nadou.config import get_settings
from nadou.models import User
from nadou.payments import xpay, xpay_push
from nadou.payments.catalog import REGULAR, VIP
from nadou.payments.config import load_pay_settings
from nadou.search import EmptyQuery, ProgramSearch


def membership_options(
    session: Session, user: User, name: str, now: datetime, *, virtual: bool
) -> list[dict]:
    try:
        result = ProgramSearch(session, None, now).search(name, user.id)
    except EmptyQuery:
        raise HTTPException(422, "请指定节目") from None
    eligible = result.summary_unlocks_by_product or {}
    options = []
    settings = get_settings()
    for product in (REGULAR, VIP):
        if not eligible.get(product.code):
            continue
        if virtual:
            ready = virtual_product_ready(product.code)
        else:
            ready = load_pay_settings(settings) is not None
        options.append({**product.public(), "purchasable": bool(user.wechat_openid) and ready})
    return options


def virtual_product_ready(code: str) -> bool:
    settings = get_settings()
    if (
        settings.xpay_env != 0
        or not settings.wx_appid
        or not settings.wx_appsecret.get_secret_value()
    ):
        return False
    product_id = settings.xpay_products.get(code)
    if not product_id or product_id != product_id.strip() or len(product_id) > 128:
        return False
    if list(settings.xpay_products.values()).count(product_id) != 1:
        return False
    try:
        xpay.configured()
        xpay_push.configured_key()
    except HTTPException:
        return False
    return True


def require_context_product(
    session: Session,
    user: User,
    name: str | None,
    tier: str | None,
    now: datetime,
    *,
    virtual: bool,
) -> None:
    if name is None:
        return
    options = membership_options(session, user, name, now, virtual=virtual)
    chosen = next((product for product in options if product["id"] == tier), None)
    if chosen is None:
        raise HTTPException(409, "此档位不能额外解锁本节目的摘要")
    if not chosen["purchasable"]:
        raise HTTPException(503, "当前渠道暂不可购买此档位")
