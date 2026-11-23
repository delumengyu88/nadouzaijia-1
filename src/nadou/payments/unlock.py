"""One-program band unlocks: what may be bought, and the entitlement a paid order grants."""

from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy.orm import Session

from nadou.bands import BAND_KINDS, BAND_LABELS, PRODUCT_BAND
from nadou.models import Entitlement, Order, User
from nadou.normalize import ProgramNormalizer
from nadou.payments.catalog import UNLOCK_PRODUCTS, PayProduct
from nadou.payments.config import live_virtual_product_id
from nadou.search import EmptyQuery, ProgramSearch

ORDER_SOURCE = "order"


def is_unlock(code: str | None) -> bool:
    return code in UNLOCK_PRODUCTS


def unlock_offer(session: Session, user: User, name: str | None, code: str, now: datetime) -> dict:
    """The band this product opens for this user and program, or an HTTP error.

    Sold only when the band is locked for the caller and has at least one record.
    """
    product = UNLOCK_PRODUCTS.get(code)
    if product is None:
        raise HTTPException(400, "档位无效")
    if not name or not name.strip():
        raise HTTPException(422, "请指定节目")
    try:
        result = ProgramSearch(session, None, now).search(name, user.id)
    except EmptyQuery:
        raise HTTPException(422, "请指定节目") from None
    band = PRODUCT_BAND[code]
    row = next((item for item in (result.bands or []) if item.band == band), None)
    if row is None or row.count == 0:
        raise HTTPException(409, "本节目这一时间段没有可解锁的记录")
    if row.state != "locked" or row.unlock is None or row.unlock.product != code:
        raise HTTPException(409, "本节目这一时间段已可查看，无需解锁")
    product_id = live_virtual_product_id(code)
    return {
        **product.public(),
        "program_name": result.program_name,
        "band": band,
        "band_label": BAND_LABELS[band],
        "count": row.count,
        "price_text": row.unlock.price_text,
        "permanent": True,
        "purchasable": bool(user.wechat_openid) and product_id is not None,
        "product_id": product_id,
    }


def canonical(name: str) -> str:
    return ProgramNormalizer().canonical(name)


def write_unlock(session: Session, order: Order, product: PayProduct | None, now: datetime):
    """Grant one permanent band entitlement for the order's program."""
    if order.tier not in BAND_KINDS or not order.program_name:
        raise ValueError("order is not a band unlock")
    entitlement = Entitlement(
        user_id=order.user_id,
        kind=order.tier,
        program_name=order.program_name,
        record_id=None,
        expires_at=now + timedelta(days=order.duration_days),
        source=ORDER_SOURCE,
    )
    session.add(entitlement)
    session.flush()
    return entitlement
