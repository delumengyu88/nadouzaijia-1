from datetime import timedelta
from uuid import uuid4

from sqlalchemy import func, select

from nadou.admin.invites import InviteStats
from nadou.admin.members import EntitlementBook
from nadou.admin.notify import FakeNotifier
from nadou.admin.orders import EntitlementIssuer, OrderGrantWriter, OrderLookup
from nadou.models import AuditLog, Entitlement, InviteCode, Order, OrderIssuance, StaffAccount, User
from tests.workbench_helpers import add_order, add_user, auth, call


class FlakyWriter:
    def __init__(self, failures: int) -> None:
        self.left = failures
        self.calls = 0

    def write(self, session, user_id: str, tier: str, days: int, now):
        self.calls += 1
        if self.left > 0:
            self.left -= 1
            raise RuntimeError("gateway down")
        return OrderGrantWriter().write(session, user_id, tier, days, now)


def test_issuance_retries_then_alerts_and_reissue_is_idempotent(session, clock):
    user = User(id="user-order-1", created_at=clock, phone="90000003001")
    session.add(user)
    order = Order(
        id="order-1",
        order_number="DD-UNIT-1",
        user_id=user.id,
        tier="vip",
        duration_days=30,
        amount=50,
        status="已支付",
        created_at=clock,
    )
    session.add(order)
    session.flush()
    notifier = FakeNotifier()
    failing = FlakyWriter(3)
    issuer = EntitlementIssuer(session, failing, notifier, clock)
    first = issuer.issue(order)
    assert first.status == "发放异常"
    assert first.attempts == 3
    assert failing.calls == 3
    assert len(notifier.messages) == 1
    assert notifier.messages[0]["title"] == "权益发放异常"
    again = issuer.issue(order)
    assert again.id == first.id
    assert failing.calls == 3

    order.tier = "regular"
    order.duration_days = 7
    recovery = FlakyWriter(0)
    recovered = EntitlementIssuer(session, recovery, notifier, clock).reissue(order)
    assert recovered.status == "issued"
    assert recovery.calls == 1
    entitlement = session.get(Entitlement, recovered.entitlement_id)
    assert entitlement.kind == "vip"
    assert entitlement.source == "order"
    assert entitlement.expires_at == clock + timedelta(days=30)
    replay = EntitlementIssuer(session, recovery, notifier, clock).reissue(order)
    assert replay.entitlement_id == recovered.entitlement_id
    assert recovery.calls == 1
    assert session.scalar(select(func.count()).select_from(Entitlement)) == 1
    assert entitlement in EntitlementBook.revenue_entitlements(session)


def test_reissue_endpoint_issues_once_and_masks_phone(workbench):
    user = add_user(workbench.session, workbench.clock.now, "90000003002")
    order = add_order(workbench.session, user, workbench.clock.now, "DD-TEST-0002")
    support_view = call(
        workbench,
        "GET",
        f"/api/v1/admin/orders/by-number/{order.order_number}",
        workbench.support_token,
    ).json()
    admin_view = call(
        workbench,
        "GET",
        f"/api/v1/admin/orders/{order.id}",
        workbench.admin_token,
    ).json()
    assert support_view["phone"] == "*******3002"
    assert admin_view["phone"] == "90000003002"
    workbench.session.expire_all()
    assert OrderLookup.by_order_number(workbench.session, "DD-TEST-0002").id == order.id

    rejected = workbench.client.post(
        f"/api/v1/admin/orders/{order.id}/reissue",
        headers=auth(workbench.support_token),
        json={"tier": "regular", "days": 1},
    )
    assert rejected.status_code == 422
    first = call(
        workbench,
        "POST",
        f"/api/v1/admin/orders/{order.id}/reissue",
        workbench.support_token,
        json={},
    ).json()
    second = call(
        workbench,
        "POST",
        f"/api/v1/admin/orders/{order.id}/reissue",
        workbench.admin_token,
        json={},
    ).json()
    assert first["issuance_status"] == "issued"
    assert first["entitlement_id"] == second["entitlement_id"]
    assert first["tier"] == "vip"
    assert first["duration_days"] == 30
    workbench.session.expire_all()
    assert (
        workbench.session.scalar(
            select(func.count()).select_from(Entitlement).where(Entitlement.user_id == user.id)
        )
        == 1
    )
    clean = add_order(
        workbench.session,
        user,
        workbench.clock.now,
        "DD-TEST-0003",
        status="已发放",
        issuance="issued",
    )
    call(
        workbench,
        "POST",
        f"/api/v1/admin/orders/{clean.id}/reissue",
        workbench.admin_token,
        json={},
        expected=409,
    )
    exported = call(workbench, "GET", "/api/v1/admin/orders/export", workbench.admin_token)
    assert "text/csv" in exported.headers["content-type"]
    assert "90000003002" in exported.text
    assert ".zip" not in exported.text
    call(workbench, "GET", "/api/v1/admin/orders/export", workbench.support_token, expected=403)
    workbench.session.expire_all()
    actions = set(workbench.session.scalars(select(AuditLog.action)).all())
    assert "reissue_entitlement" in actions
    issuance = workbench.session.scalar(
        select(OrderIssuance).where(OrderIssuance.order_id == order.id)
    )
    assert issuance.tier == "vip"
    assert issuance.duration_days == 30


def test_partial_retry_succeeds_on_the_third_attempt(session, clock):
    user = User(id="user-order-2", created_at=clock, phone="90000003003")
    session.add(user)
    order = Order(
        id="order-2",
        order_number="DD-UNIT-2",
        user_id=user.id,
        tier="regular",
        duration_days=15,
        amount=20,
        status="已支付",
        created_at=clock,
    )
    session.add(order)
    session.flush()
    writer = FlakyWriter(2)
    notifier = FakeNotifier()
    issued = EntitlementIssuer(session, writer, notifier, clock).issue(order)
    assert issued.status == "issued"
    assert writer.calls == 3
    assert notifier.messages == []
    EntitlementIssuer(session, writer, notifier, clock).issue(order)
    assert writer.calls == 3
    assert session.scalar(select(func.count()).select_from(Entitlement)) == 1


def test_test_product_orders_are_left_out_of_revenue_and_conversion(session, clock):
    staff = StaffAccount(
        id="staff-stats",
        username="stats-admin",
        password_hash="unused",
        role="admin",
        created_at=clock,
    )
    formal_user = User(id="user-formal", created_at=clock, phone="90000008801")
    test_user = User(id="user-test-sku", created_at=clock, phone="90000008802")
    session.add_all([staff, formal_user, test_user])
    session.flush()

    def sell(user, number, product, tier, amount, days, channel, code):
        session.add(
            InviteCode(
                id=str(uuid4()),
                code=code,
                channel=channel,
                created_by=staff.id,
                created_at=clock,
                expires_at=clock + timedelta(days=30),
                redeemed_by=user.id,
                redeemed_at=clock,
            )
        )
        order = Order(
            id=str(uuid4()),
            order_number=number,
            user_id=user.id,
            tier=tier,
            duration_days=days,
            amount=amount,
            product=product,
            status="已发放",
            created_at=clock,
            paid_at=clock,
        )
        session.add(order)
        session.flush()
        grant = Entitlement(
            user_id=user.id,
            kind=tier,
            program_name=None,
            record_id=None,
            expires_at=clock + timedelta(days=days),
            source="order",
        )
        session.add(grant)
        session.flush()
        session.add(
            OrderIssuance(
                order_id=order.id,
                status="issued",
                attempts=1,
                tier=tier,
                duration_days=days,
                entitlement_id=grant.id,
                last_error=None,
                reissued=False,
                created_at=clock,
                updated_at=clock,
            )
        )
        return grant

    formal = sell(
        formal_user, "ND-FORMAL-1", "membership_vip", "vip", 599, 30, "正式渠道", "FORMAL23"
    )
    trial = sell(test_user, "ND-TEST-1", "membership_test", "regular", 0, 1, "测试渠道", "TEST2345")
    session.commit()

    revenue_ids = {row.id for row in EntitlementBook.revenue_entitlements(session)}
    assert formal.id in revenue_ids
    assert trial.id not in revenue_ids
    stats = {row["channel"]: row for row in InviteStats.channels(session, clock)}
    assert stats["正式渠道"]["paid_within_30_days"] == 1
    assert stats["测试渠道"]["paid_within_30_days"] == 0
