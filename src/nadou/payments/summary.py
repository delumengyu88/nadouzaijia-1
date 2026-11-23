"""Membership checkout opened from a display-only program summary."""

from datetime import datetime

from fastapi import HTTPException
from sqlalchemy.orm import Session

from nadou.config import get_settings
from nadou.models import User
from nadou.payments.catalog import DIAMOND, REGULAR, VIP
from nadou.payments.config import diamond_product_id, live_virtual_product_id, load_pay_settings
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
            ready = live_virtual_product_id(product.code) is not None
        else:
            ready = load_pay_settings(settings) is not None
        options.append({**product.public(), "purchasable": bool(user.wechat_openid) and ready})
    if virtual and user.wechat_openid and eligible.get(VIP.code) and diamond_product_id():
        options.append({**DIAMOND.public(), "purchasable": True, "unlock_product": VIP.code})
    return options


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
