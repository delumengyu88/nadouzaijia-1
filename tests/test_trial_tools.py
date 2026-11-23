"""Experience-build tier switch. Rejections match a missing route."""

from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from nadou.admin.members import EntitlementBook
from nadou.config import Settings, get_settings
from nadou.devtools.trial_tools import DEV_PRODUCT
from nadou.entitlements import AccessGrants, EntitlementPolicy
from nadou.models import (
    Entitlement,
    IndexRecord,
    InviteCode,
    Order,
    OrderIssuance,
    StaffAccount,
    User,
)
from nadou.payments.catalog import TEST_PRODUCTS
from nadou.payments.config import user_may_use_test_tier
from nadou.payments.service import PayError, PaymentService

PATH = "/api/v1/dev/tier"
MISSING = {"detail": "Not Found"}
FORGED_HEADERS = {
    "Referer": "https://servicewechat.com/wx-trial/0/page-frame.html",
    "X-Env-Version": "trial",
    "envVersion": "trial",
}


@pytest.fixture(autouse=True)
def _reset_settings(monkeypatch):
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def configure(monkeypatch, tools, users, *, pay_tier="1"):
    if tools is None:
        monkeypatch.delenv("NADOU_TRIAL_TOOLS", raising=False)
    else:
        monkeypatch.setenv("NADOU_TRIAL_TOOLS", tools)
    monkeypatch.setenv("NADOU_WECHAT_PAY_TEST_TIER", pay_tier)
    monkeypatch.setenv("NADOU_WECHAT_PAY_TEST_USERS", users)
    monkeypatch.delenv("NADOU_TRIAL_ACCOUNTS", raising=False)
    monkeypatch.delenv("NADOU_WECHAT_PAY_TEST_ACCOUNTS", raising=False)
    get_settings.cache_clear()


def add_user(session, clock, user_id, openid):
    row = User(id=user_id, created_at=clock, wechat_openid=openid)
    session.add(row)
    session.commit()
    return row


def post_tier(workbench, user_id, tier, *, headers=None, body=None, content=None):
    extra = {
        "X-Env-Version": "release",
        "Referer": "https://servicewechat.com/wx-release/devtools/page-frame.html",
    }
    if headers:
        extra.update(headers)
    if user_id is not None:
        extra.update(workbench.client.auth(user_id))
    payload = {"tier": tier, "envVersion": "release"}
    if body:
        payload.update(body)
    kwargs = {"headers": extra}
    if content is None:
        kwargs["json"] = payload
    else:
        kwargs["content"] = content
        extra["Content-Type"] = "application/json"
    return workbench.client.post(PATH, **kwargs)


def membership_decision(session, user_id, now, age):
    session.expire_all()
    rows = session.scalars(
        select(Entitlement).where(
            Entitlement.user_id == user_id,
            Entitlement.kind.in_(("regular", "vip")),
            Entitlement.expires_at > now,
        )
    ).all()
    grants = AccessGrants(
        vip=any(row.kind == "vip" for row in rows),
        regular=any(row.kind == "regular" for row in rows),
    )
    return EntitlementPolicy.decide(age, grants)


def test_dev_product_shares_the_pay_test_stats_set():
    assert "trial_accounts" not in Settings.model_fields
    assert "wechat_pay_test_accounts" not in Settings.model_fields
    assert DEV_PRODUCT in TEST_PRODUCTS
    assert "membership_test" in TEST_PRODUCTS


def test_closed_switch_is_the_same_404_as_a_missing_route(workbench, session, clock, monkeypatch):
    user = add_user(session, clock, "trial-closed", "openid-closed")
    configure(monkeypatch, "0", user.id)
    missing = workbench.client.post("/api/v1/no-such-route", json={"tier": "vip"})
    denied = post_tier(
        workbench, user.id, "vip", headers=FORGED_HEADERS, body={"envVersion": "trial"}
    )
    assert user_may_use_test_tier(user.id) is True
    assert denied.status_code == missing.status_code == 404
    assert denied.json() == missing.json() == MISSING
    assert "/api/v1/dev/tier" not in workbench.client.get("/openapi.json").json()["paths"]
    session.expire_all()
    assert session.scalar(select(func.count()).select_from(Order)) == 0


@pytest.mark.parametrize("tools", [None, "0", "", "true", "yes", "TRUE", "2"])
def test_only_exact_one_opens_the_switch(workbench, session, clock, monkeypatch, tools):
    user = add_user(session, clock, f"trial-off-{tools}", f"openid-off-{tools}")
    configure(monkeypatch, tools, user.id)
    response = post_tier(
        workbench,
        user.id,
        "vip",
        headers=FORGED_HEADERS,
        body={"envVersion": "trial"},
        content=b"{",
    )
    assert response.status_code == 404
    assert response.json() == MISSING


def test_shared_allowlist_rejects_outsiders_and_anonymous(workbench, session, clock, monkeypatch):
    allowed = add_user(session, clock, "trial-allowed", "openid-allowed")
    outsider = add_user(session, clock, "trial-outsider", "openid-outsider")
    configure(monkeypatch, "1", "trial-allowed, openid-outsider")
    assert user_may_use_test_tier(allowed.id) is True
    assert user_may_use_test_tier(outsider.id) is False

    forged = workbench.client.post(
        PATH,
        headers={**workbench.client.auth(outsider.id), **FORGED_HEADERS},
        json={"tier": "vip", "envVersion": "trial", "env_version": "trial"},
    )
    anonymous = workbench.client.post(
        PATH,
        headers=FORGED_HEADERS,
        json={"tier": "vip", "envVersion": "trial"},
    )
    invalid = workbench.client.post(
        PATH,
        headers={"Authorization": "Bearer not-a-session", **FORGED_HEADERS},
        json={"tier": "regular"},
    )
    missing = workbench.client.post("/api/v1/does-not-exist")
    assert forged.status_code == anonymous.status_code == invalid.status_code == 404
    assert forged.json() == anonymous.json() == invalid.json() == missing.json() == MISSING

    payments = PaymentService(lambda: clock, workbench.notifier, None)
    shown = [item["id"] for item in payments.list_tiers(outsider)["tiers"]]
    assert "membership_test" not in shown
    with pytest.raises(PayError) as denied_pay:
        payments.create(session, outsider, "membership_test")
    assert denied_pay.value.status_code == 400
    assert denied_pay.value.detail == "档位无效"
    allowed_tiers = [item["id"] for item in payments.list_tiers(allowed)["tiers"]]
    assert allowed_tiers[-1] == "membership_test"
    with pytest.raises(PayError) as unconfigured:
        payments.create(session, allowed, "membership_test")
    assert unconfigured.value.status_code == 503

    opened = post_tier(workbench, allowed.id, "vip", headers={"envVersion": "develop"})
    assert opened.status_code == 200, opened.text
    assert opened.json()["tier"] == "vip"


def test_tier_switch_changes_membership_and_marks_orders(workbench, session, clock, monkeypatch):
    user = add_user(session, clock, "trial-member", "openid-member")
    session.add(
        Entitlement(
            user_id=user.id,
            kind="program_30",
            program_name="示例节目",
            record_id=None,
            expires_at=clock + timedelta(days=10),
            source="manual",
        )
    )
    session.commit()
    configure(monkeypatch, "1", " other-id , trial-member ")
    expects = [
        ("free", "free", {40: None, 100: None, 200: "lowres"}, 0),
        ("regular", "regular", {40: None, 100: "full", 200: "full"}, 1),
        ("vip", "vip", {40: "full", 100: "full", 200: "full"}, 2),
        ("regular", "regular", {40: None, 100: "full", 200: "full"}, 3),
        ("free", "free", {40: None, 100: None, 200: "lowres"}, 3),
    ]
    for tier, shown, access, orders in expects:
        response = post_tier(workbench, user.id, tier)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["tier"] == shown
        if shown == "free":
            assert body["expires_at"] is None
        else:
            assert datetime.fromisoformat(body["expires_at"]) == clock + timedelta(days=30)
        me = workbench.client.get("/api/v1/auth/me", headers=workbench.client.auth(user.id)).json()
        assert me["tier"] == shown
        if shown == "vip":
            assert datetime.fromisoformat(me["vip_expires_at"]) == clock + timedelta(days=30)
            assert me["regular_expires_at"] is None
        elif shown == "regular":
            assert datetime.fromisoformat(me["regular_expires_at"]) == clock + timedelta(days=30)
            assert me["vip_expires_at"] is None
        else:
            assert me["vip_expires_at"] is None
            assert me["regular_expires_at"] is None
        session.expire_all()
        for age, expected in access.items():
            decision = membership_decision(session, user.id, clock, age)
            assert decision.access == expected
            assert decision.can_download_report is (expected == "full")
        assert session.scalar(select(func.count()).select_from(Order)) == orders
        live = session.scalars(
            select(Entitlement).where(
                Entitlement.user_id == user.id,
                Entitlement.kind.in_(("regular", "vip")),
                Entitlement.expires_at > clock,
            )
        ).all()
        assert [row.kind for row in live] == ([] if shown == "free" else [shown])

    session.expire_all()
    marked = session.scalars(select(Order).where(Order.user_id == user.id)).all()
    assert {order.product for order in marked} == {DEV_PRODUCT}
    assert {order.amount for order in marked} == {0}
    assert {order.status for order in marked} == {"已发放"}
    assert {order.duration_days for order in marked} == {30}
    assert {order.created_at for order in marked} == {clock}
    assert {order.paid_at for order in marked} == {clock}
    assert session.scalar(select(func.count()).select_from(IndexRecord)) == 0
    program = session.scalar(
        select(Entitlement).where(
            Entitlement.user_id == user.id,
            Entitlement.kind == "program_30",
        )
    )
    assert program.expires_at == clock + timedelta(days=10)


def test_invalid_tier_is_400_only_after_both_gates_open(workbench, session, clock, monkeypatch):
    user = add_user(session, clock, "trial-bad-tier", "openid-bad-tier")
    configure(monkeypatch, "1", "someone-else")
    hidden = post_tier(
        workbench, user.id, "gold", body={"envVersion": "trial"}, content=b"not-json"
    )
    assert hidden.status_code == 404
    assert hidden.json() == MISSING
    configure(monkeypatch, "1", user.id)
    rejected = post_tier(workbench, user.id, "gold")
    assert rejected.status_code == 400
    assert rejected.json()["detail"] == "tier must be free, regular, or vip"


def test_dev_orders_stay_out_of_membership_and_revenue_stats(
    workbench, session, clock, monkeypatch
):
    from nadou.admin.orders import OrderGrantWriter
    from tests.workbench_helpers import call

    user = add_user(session, clock, "trial-stats", "openid-stats")
    admin_id = session.scalar(select(StaffAccount.id).where(StaffAccount.username == "wb-admin"))
    session.add(
        InviteCode(
            id=str(uuid4()),
            code="STAT0001",
            channel="体验渠道",
            created_by=admin_id,
            created_at=clock,
            expires_at=clock + timedelta(days=30),
            redeemed_by=user.id,
            redeemed_at=clock,
        )
    )
    session.commit()
    configure(monkeypatch, "1", user.id)
    switched = post_tier(workbench, user.id, "vip")
    assert switched.status_code == 200, switched.text
    session.expire_all()
    dev = session.scalar(select(Order).where(Order.product == DEV_PRODUCT))
    dev_grant = session.get(Entitlement, _issuance(session, dev).entitlement_id)
    assert dev_grant.source == "order"
    assert dev_grant not in EntitlementBook.revenue_entitlements(session)

    real = Order(
        id=str(uuid4()),
        order_number="DD-REAL-0001",
        user_id=user.id,
        tier="vip",
        duration_days=30,
        amount=599,
        amount_fen=59900,
        product="membership_vip",
        status="已发放",
        created_at=clock,
        client="小程序",
        paid_at=clock,
    )
    session.add(real)
    session.flush()
    real_grant = OrderGrantWriter().write(session, user.id, "vip", 30, clock)
    session.add(
        OrderIssuance(
            order_id=real.id,
            status="issued",
            attempts=1,
            tier="vip",
            duration_days=30,
            entitlement_id=real_grant.id,
            last_error=None,
            reissued=False,
            created_at=clock,
            updated_at=clock,
        )
    )
    session.commit()
    session.expire_all()
    revenue_ids = {row.id for row in EntitlementBook.revenue_entitlements(session)}
    assert real_grant.id in revenue_ids
    assert dev_grant.id not in revenue_ids
    stats = call(workbench, "GET", "/api/v1/admin/invite-codes/stats", workbench.admin_token).json()
    channel = next(row for row in stats if row["channel"] == "体验渠道")
    assert channel["paid_within_30_days"] == 1
    listed = call(workbench, "GET", "/api/v1/admin/orders", workbench.admin_token).json()
    numbers = {row["order_number"] for row in listed}
    assert dev.order_number in numbers
    assert "DD-REAL-0001" in numbers


def _issuance(session, order):
    return session.scalar(select(OrderIssuance).where(OrderIssuance.order_id == order.id))
