"""Paid membership starts at max(this tier's live expiry, now)."""

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from nadou.admin.notify import FakeNotifier
from nadou.admin.orders import EntitlementIssuer, OrderGrantWriter
from nadou.models import Entitlement, Order, User


def add_user(session, clock, user_id: str) -> User:
    user = User(id=user_id, created_at=clock, phone="9000000" + user_id[-4:])
    session.add(user)
    session.flush()
    return user


def add_grant(session, user: User, when, kind: str, source: str | None = "order") -> Entitlement:
    row = Entitlement(
        user_id=user.id,
        kind=kind,
        program_name=None,
        record_id=None,
        expires_at=when,
        source=source,
    )
    session.add(row)
    session.flush()
    return row


def add_order(session, user: User, clock, number: str, tier: str, days: int) -> Order:
    order = Order(
        id=number,
        order_number=number,
        user_id=user.id,
        tier=tier,
        duration_days=days,
        amount=599 if tier == "vip" else 299,
        status="已支付",
        created_at=clock,
    )
    session.add(order)
    session.flush()
    return order


def issue(session, clock, order: Order):
    return EntitlementIssuer(session, OrderGrantWriter(), FakeNotifier(), clock).issue(order)


def granted(session, issuance) -> Entitlement:
    return session.get(Entitlement, issuance.entitlement_id)


def test_renewal_expiry_picks_the_later_start(clock):
    later = clock + timedelta(days=10)
    assert OrderGrantWriter.renewal_expiry(clock, 30, None) == clock + timedelta(days=30)
    assert OrderGrantWriter.renewal_expiry(clock, 30, later) == clock + timedelta(days=40)
    assert OrderGrantWriter.renewal_expiry(clock, 30, clock - timedelta(days=3)) == (
        clock + timedelta(days=30)
    )
    assert OrderGrantWriter.renewal_expiry(clock, 30, clock) == clock + timedelta(days=30)
    assert OrderGrantWriter.renewal_expiry(clock, 1, later) == clock + timedelta(days=11)


def test_first_purchase_starts_from_now(session, clock):
    user = add_user(session, clock, "user-renew-first")
    order = add_order(session, user, clock, "ND-RENEW-FIRST", "regular", 30)
    issued = issue(session, clock, order)
    assert granted(session, issued).expires_at == clock + timedelta(days=30)
    assert granted(session, issued).kind == "regular"
    assert granted(session, issued).source == "order"
    assert session.scalar(select(func.count()).select_from(Entitlement)) == 1


def test_unexpired_renewal_keeps_remaining_days(session, clock):
    user = add_user(session, clock, "user-renew-open")
    current = add_grant(session, user, clock + timedelta(days=10), "regular")
    order = add_order(session, user, clock, "ND-RENEW-OPEN", "regular", 30)
    issued = issue(session, clock, order)
    assert granted(session, issued).expires_at == clock + timedelta(days=40)
    assert current.expires_at == clock + timedelta(days=10)
    assert granted(session, issued).id != current.id


def test_expired_renewal_starts_from_now(session, clock):
    user = add_user(session, clock, "user-renew-past")
    lapsed = add_grant(session, user, clock - timedelta(days=1), "regular")
    order = add_order(session, user, clock, "ND-RENEW-PAST", "regular", 30)
    issued = issue(session, clock, order)
    assert granted(session, issued).kind == "regular"
    assert granted(session, issued).expires_at == clock + timedelta(days=30)
    assert lapsed.expires_at == clock - timedelta(days=1)


def test_expiry_equal_to_now_is_not_a_base(session, clock):
    user = add_user(session, clock, "user-renew-now")
    ending = add_grant(session, user, clock, "regular")
    assert OrderGrantWriter.live_tier_expiry(session, user.id, "regular", clock) is None
    order = add_order(session, user, clock, "ND-RENEW-NOW", "regular", 30)
    issued = issue(session, clock, order)
    assert granted(session, issued).expires_at == clock + timedelta(days=30)
    assert ending.expires_at == clock


def test_repeat_issue_does_not_stack(session, clock):
    user = add_user(session, clock, "user-renew-once")
    add_grant(session, user, clock + timedelta(days=10), "vip")
    order = add_order(session, user, clock, "ND-RENEW-ONCE", "vip", 30)
    first = issue(session, clock, order)
    assert granted(session, first).expires_at == clock + timedelta(days=40)
    second = EntitlementIssuer(session, OrderGrantWriter(), FakeNotifier(), clock).issue(order)
    assert second.id == first.id
    assert second.entitlement_id == first.entitlement_id
    assert granted(session, second).expires_at == clock + timedelta(days=40)
    assert (
        session.scalar(
            select(func.count()).select_from(Entitlement).where(Entitlement.user_id == user.id)
        )
        == 2
    )


def test_later_same_tier_expiry_is_the_base_and_other_tiers_stay(session, clock):
    user = add_user(session, clock, "user-renew-tier")
    earlier = add_grant(session, user, clock + timedelta(days=10), "regular", "order")
    invite = add_grant(session, user, clock + timedelta(days=18), "regular", "invite")
    vip = add_grant(session, user, clock + timedelta(days=40), "vip", "manual")
    program = Entitlement(
        user_id=user.id,
        kind="program_30",
        program_name="示例节目",
        record_id=None,
        expires_at=clock + timedelta(days=5),
        source="order",
    )
    session.add(program)
    session.flush()
    order = add_order(session, user, clock, "ND-RENEW-TIER", "regular", 30)
    issued = issue(session, clock, order)
    assert granted(session, issued).expires_at == clock + timedelta(days=48)
    assert earlier.expires_at == clock + timedelta(days=10)
    assert invite.expires_at == clock + timedelta(days=18)
    assert vip.expires_at == clock + timedelta(days=40)
    assert program.expires_at == clock + timedelta(days=5)


def test_buying_vip_does_not_use_or_change_regular(session, clock):
    user = add_user(session, clock, "user-renew-vip")
    regular = add_grant(session, user, clock + timedelta(days=20), "regular")
    order = add_order(session, user, clock, "ND-RENEW-VIP", "vip", 30)
    issued = issue(session, clock, order)
    assert granted(session, issued).kind == "vip"
    assert granted(session, issued).expires_at == clock + timedelta(days=30)
    assert regular.expires_at == clock + timedelta(days=20)
    follow = add_order(session, user, clock, "ND-RENEW-REG", "regular", 30)
    extended = issue(session, clock, follow)
    assert granted(session, extended).expires_at == clock + timedelta(days=50)
    assert granted(session, issued).expires_at == clock + timedelta(days=30)


def test_refunding_partly_consumed_early_order_preserves_later_paid_duration(session, clock):
    from nadou.admin.audit import AuditWriter
    from nadou.admin.errors import WorkbenchError
    from nadou.admin.orders import OrderRefund

    user = add_user(session, clock, "refund-chain")
    original = add_order(session, user, clock, "CHAIN-1", "regular", 30)
    first = granted(session, issue(session, clock, original))
    fallback = add_grant(session, user, clock + timedelta(days=20), "regular", source="invite")
    later = add_order(session, user, clock, "CHAIN-2", "regular", 30)
    second = granted(session, issue(session, clock, later))
    third = granted(
        session, issue(session, clock, add_order(session, user, clock, "CHAIN-3", "regular", 10))
    )
    vip = add_grant(session, user, clock + timedelta(days=70), "vip", source="manual")
    refund_at = clock + timedelta(days=11)
    refund = OrderRefund(session, AuditWriter(session, refund_at, None, None), refund_at)
    refund.refund(original, "整单退款")
    assert first.expires_at == refund_at
    assert fallback.expires_at == clock + timedelta(days=20)
    assert second.expires_at == clock + timedelta(days=50)
    assert third.expires_at == clock + timedelta(days=60)
    assert vip.expires_at == clock + timedelta(days=70)
    with pytest.raises(WorkbenchError):
        refund.refund(original, "重复请求")
    assert third.expires_at == clock + timedelta(days=60)


@pytest.mark.parametrize("legacy", ["no_issuance", "manual_override", "expired"])
def test_refund_leaves_unrelated_or_unproven_access_alone(session, clock, legacy):
    from nadou.admin.audit import AuditWriter
    from nadou.admin.orders import OrderRefund

    user = add_user(session, clock, "refund-legacy")
    first = add_order(session, user, clock, "LEGACY", "regular", 30)
    if legacy == "no_issuance":
        first.status = "已发放"
    else:
        old = granted(session, issue(session, clock, first))
        if legacy == "manual_override":
            old.source = "manual"
            old.expires_at += timedelta(days=10)
        else:
            old.expires_at = clock
    other = granted(
        session, issue(session, clock, add_order(session, user, clock, "OTHER", "regular", 10))
    )
    expiry = other.expires_at
    OrderRefund(session, AuditWriter(session, clock, None, None), clock).refund(first, "整单退款")
    assert other.expires_at == expiry
    if legacy == "manual_override":
        assert old.expires_at == clock + timedelta(days=40)
