"""Experience-build membership switch. Not part of the production API.

The route opens only when ``NADOU_TRIAL_TOOLS`` is exactly ``1`` and
``user_may_use_test_tier`` allows the signed-in user. That function is the
same gate as the 0.01 yuan ``membership_test`` tier: ``NADOU_WECHAT_PAY_TEST_TIER``
on, and the user id in ``NADOU_WECHAT_PAY_TEST_USERS``. There is no second list.

The server never reads the mini program's version or environment.

Delete this package before the official mini program is submitted for review,
and remove only the call sites below with it:

- ``src/nadou/main.py`` — ``register_trial_tools(...)``
- ``src/nadou/config.py`` — ``trial_tools``
- ``tests/test_trial_tools.py``
- the 体验版会员档位开关 section in ``docs/DESIGN.md``, plus the README note

Leave ``membership_dev`` in ``TEST_PRODUCTS`` and in ``ck_orders_product``.
Those rows are historical orders. Dropping the product from the set would
count them as paid conversions again. Tightening the check constraint needs
another migration, which fails while any ``membership_dev`` row remains.
"""

from datetime import datetime
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.orm import Session

from nadou.admin.orders import OrderGrantWriter
from nadou.auth import UserSessions
from nadou.config import Settings, get_settings
from nadou.models import Entitlement, Order, OrderIssuance, User
from nadou.payments.config import user_may_use_test_tier

# New value on the existing orders.product check. Not a second is_test column.
DEV_PRODUCT = "membership_dev"
TRIAL_DAYS = 30
TIERS = frozenset({"free", "regular", "vip"})
_MISSING = "Not Found"


def trial_tools_enabled(settings: Settings | None = None) -> bool:
    """Only the exact value ``1`` opens the route. Anything else stays closed."""
    current = settings if settings is not None else get_settings()
    return current.trial_tools.strip() == "1"


class TrialTierSwitch:
    """Point the signed-in account at one membership tier for 30 days.

    ``free`` expires live 普通 and VIP grants and writes no order. ``regular`` and
    ``vip`` expire those grants, then open the chosen tier through
    ``OrderGrantWriter`` and one ``membership_dev`` order. No backdated rows.
    Expiry is immediate and is not restored, including a real paid membership.
    The allowlist is for test accounts only.
    """

    def apply(self, session: Session, user: User, tier: str, now: datetime) -> dict:
        if tier not in TIERS:
            raise HTTPException(status_code=400, detail="tier must be free, regular, or vip")
        self._clear_membership(session, user.id, now)
        if tier == "free":
            return {"tier": "free", "expires_at": None}
        grant = self._grant(session, user, tier, now)
        return {"tier": tier, "expires_at": grant.expires_at.isoformat()}

    @staticmethod
    def _clear_membership(session: Session, user_id: str, now: datetime) -> None:
        rows = session.scalars(
            select(Entitlement).where(
                Entitlement.user_id == user_id,
                Entitlement.kind.in_(("regular", "vip")),
                Entitlement.expires_at > now,
            )
        ).all()
        for row in rows:
            row.expires_at = now
        session.flush()

    @staticmethod
    def _grant(session: Session, user: User, tier: str, now: datetime) -> Entitlement:
        order = Order(
            id=str(uuid4()),
            order_number="D" + uuid4().hex[:20],
            user_id=user.id,
            tier=tier,
            duration_days=TRIAL_DAYS,
            amount=0,
            status="已发放",
            created_at=now,
            client=None,
            paid_at=now,
            refunded_at=None,
            refund_reason=None,
            product=DEV_PRODUCT,
            amount_fen=None,
            prepay_id=None,
            wechat_transaction_id=None,
        )
        session.add(order)
        session.flush()
        grant = OrderGrantWriter().write(session, user.id, tier, TRIAL_DAYS, now)
        session.add(
            OrderIssuance(
                order_id=order.id,
                status="issued",
                attempts=1,
                tier=tier,
                duration_days=TRIAL_DAYS,
                entitlement_id=grant.id,
                last_error=None,
                reissued=False,
                created_at=now,
                updated_at=now,
            )
        )
        session.flush()
        return grant


def register_trial_tools(app: FastAPI, get_session, clock) -> None:
    """Mount the switch. Every rejection uses the same 404 as a missing route."""
    session_dependency = Depends(get_session, scope="function")

    @app.api_route(
        "/api/v1/dev/tier",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
        include_in_schema=False,
    )
    async def switch_trial_tier(
        request: Request,
        authorization: str | None = Header(default=None),
        session: Session = session_dependency,
    ):
        # Identity is the bearer session only. No other header, query, or body
        # field (including a forged experience-build flag) is consulted.
        if request.method != "POST" or not trial_tools_enabled():
            raise HTTPException(status_code=404, detail=_MISSING)
        # DB work runs in the threadpool so a row-lock wait cannot stall the event loop.
        user = await run_in_threadpool(_user_or_missing, session, authorization, clock())
        if not user_may_use_test_tier(user.id):
            raise HTTPException(status_code=404, detail=_MISSING)
        tier = await _tier_name(request)
        return await run_in_threadpool(TrialTierSwitch().apply, session, user, tier, clock())


def _user_or_missing(session: Session, authorization: str | None, now: datetime) -> User:
    try:
        return UserSessions.resolve(session, authorization, now)
    except HTTPException:
        raise HTTPException(status_code=404, detail=_MISSING) from None


async def _tier_name(request: Request) -> str:
    try:
        payload = await request.json()
    except (UnicodeDecodeError, ValueError):
        payload = None
    tier = payload.get("tier") if isinstance(payload, dict) else None
    if not isinstance(tier, str) or tier not in TIERS:
        raise HTTPException(status_code=400, detail="tier must be free, regular, or vip")
    return tier
