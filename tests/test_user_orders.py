from datetime import UTC, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from nadou.models import Order, User

BEIJING = ZoneInfo("Asia/Shanghai")
LIST_KEYS = {"orderNo", "title", "line", "status", "statusText", "product", "isTest"}
DETAIL_KEYS = {
    "order_number",
    "tier",
    "title",
    "membership",
    "duration_days",
    "amount_fen",
    "status",
    "paid_at",
    "granted",
    "orderNo",
    "line",
    "statusText",
    "product",
    "isTest",
}
SECRET_PREPAY = "prepay-do-not-leak"
SECRET_TXN = "wx-tx-do-not-leak"
SECRET_REFUND = "refund-note-do-not-leak"
SECRET_OPENID = "openid-do-not-leak"
SECRET_ID = "internal-order-id"


def add_order(session, user, when, number, **fields) -> Order:
    order = Order(
        id=fields.pop("id", str(uuid4())),
        order_number=number,
        user_id=user.id,
        tier=fields.pop("tier", "vip"),
        duration_days=fields.pop("duration_days", 30),
        amount=fields.pop("amount", 0),
        status=fields.pop("status"),
        created_at=when,
        client=fields.pop("client", None),
        paid_at=fields.pop("paid_at", None),
        refunded_at=fields.pop("refunded_at", None),
        refund_reason=fields.pop("refund_reason", None),
        product=fields.pop("product", None),
        amount_fen=fields.pop("amount_fen", None),
        prepay_id=fields.pop("prepay_id", None),
        wechat_transaction_id=fields.pop("wechat_transaction_id", None),
    )
    if fields:
        raise AssertionError(f"unused order fields: {sorted(fields)}")
    session.add(order)
    session.commit()
    return order


def test_anonymous_order_list_is_401(client, session, clock):
    session.add(User(id="victim-orders", created_at=clock))
    session.commit()
    bare = client.get("/api/v1/pay/orders", headers={"Authorization": ""})
    bogus = client.get(
        "/api/v1/pay/orders",
        headers={"Authorization": "Bearer not-a-session", "X-User-Id": "victim-orders"},
    )
    assert bare.status_code == 401
    assert bogus.status_code == 401
    assert bare.json()["detail"] == "authentication required"
    assert bogus.json()["detail"] == "authentication required"
    assert bare.headers["www-authenticate"] == "Bearer"
    assert bare.json() != []


def test_banned_account_cannot_list_orders(client, session, clock):
    user = User(id="banned-orders", created_at=clock, ban_type="permanent")
    session.add(user)
    session.commit()
    response = client.get("/api/v1/pay/orders", headers=client.auth(user.id))
    assert response.status_code == 403
    assert response.json()["detail"] == "account is disabled"


def test_empty_order_list_is_an_array(client):
    response = client.get("/api/v1/pay/orders")
    assert response.status_code == 200
    assert response.json() == []


def test_list_is_own_orders_newest_first(client, session, clock):
    owner = session.get(User, "test-default-free")
    other = User(id="other-orders", created_at=clock, wechat_openid=SECRET_OPENID)
    session.add(other)
    session.commit()
    newest_at = clock.replace(hour=0, minute=30) + timedelta(days=1)
    add_order(
        session,
        owner,
        clock - timedelta(days=2),
        "ND-OLD-REG1",
        status="待支付",
        tier="regular",
        product="membership_regular",
        amount=299,
        amount_fen=29900,
    )
    add_order(
        session,
        owner,
        clock,
        "ND-SAME-AAA",
        id="id-aaa-same-time",
        status="已发放",
        product="membership_vip",
        amount=599,
        amount_fen=59900,
        paid_at=clock,
    )
    add_order(
        session,
        owner,
        clock,
        "ND-SAME-ZZZ",
        id="id-zzz-same-time",
        status="已发放",
        product="membership_vip",
        amount=599,
        amount_fen=59900,
        paid_at=clock,
    )
    add_order(
        session,
        owner,
        newest_at,
        "ND-NEW-TEST",
        status="待支付",
        tier="regular",
        duration_days=1,
        product="membership_test",
        amount=0,
        amount_fen=1,
        prepay_id=SECRET_PREPAY,
    )
    add_order(
        session,
        other,
        newest_at + timedelta(minutes=5),
        "ND-OTHER01",
        status="已发放",
        product="membership_vip",
        amount=599,
        amount_fen=59900,
        prepay_id=SECRET_PREPAY,
        wechat_transaction_id=SECRET_TXN,
        refund_reason=SECRET_REFUND,
    )

    response = client.get("/api/v1/pay/orders")
    assert response.status_code == 200
    body = response.json()
    assert [row["orderNo"] for row in body] == [
        "ND-NEW-TEST",
        "ND-SAME-ZZZ",
        "ND-SAME-AAA",
        "ND-OLD-REG1",
    ]
    assert "ND-OTHER01" not in response.text
    assert SECRET_OPENID not in response.text
    assert SECRET_PREPAY not in response.text
    newest = body[0]
    assert set(newest) == LIST_KEYS
    assert newest == {
        "orderNo": "ND-NEW-TEST",
        "title": "支付测试",
        "line": "待支付 · ¥0.01 · 2026-11-22",
        "status": "pending",
        "statusText": "待支付",
        "product": "membership_test",
        "isTest": True,
    }
    assert "2026-11-21" not in newest["line"]
    assert body[-1]["title"] == "普通会员"
    assert body[-1]["line"] == "待支付 · ¥299 · 2026-11-19"
    assert body[-1]["isTest"] is False
    assert body[1]["isTest"] is False


def test_test_products_and_statuses_are_returned_as_stored(client, session, clock):
    owner = session.get(User, "test-default-free")
    rows = [
        ("ND-DEV-0001", "已发放", "membership_dev", None, 0, "regular"),
        ("ND-PAID-001", "已支付", "membership_vip", 59900, 599, "vip"),
        ("ND-REFUND01", "已退款", "membership_regular", 29900, 299, "regular"),
        ("ND-GRANTERR", "发放异常", "membership_vip", 59900, 599, "vip"),
        ("ND-NOPRODUCT", "待支付", None, None, 10, "vip"),
    ]
    for index, (number, status, product, amount_fen, amount, tier) in enumerate(rows):
        add_order(
            session,
            owner,
            clock - timedelta(minutes=index),
            number,
            status=status,
            tier=tier,
            product=product,
            amount=amount,
            amount_fen=amount_fen,
        )
    body = client.get("/api/v1/pay/orders").json()
    by_no = {row["orderNo"]: row for row in body}
    assert by_no["ND-DEV-0001"]["title"] is None
    assert by_no["ND-DEV-0001"]["product"] == "membership_dev"
    assert by_no["ND-DEV-0001"]["isTest"] is True
    assert by_no["ND-DEV-0001"]["status"] == "granted"
    assert by_no["ND-DEV-0001"]["statusText"] == "已发放"
    assert by_no["ND-DEV-0001"]["line"] == "已发放 · ¥0 · 2026-11-21"
    assert by_no["ND-PAID-001"]["status"] == "paid"
    assert by_no["ND-PAID-001"]["statusText"] == "已支付"
    assert by_no["ND-PAID-001"]["isTest"] is False
    assert by_no["ND-REFUND01"]["status"] == "refunded"
    assert by_no["ND-REFUND01"]["statusText"] == "已退款"
    assert by_no["ND-GRANTERR"]["status"] == "grant_error"
    assert by_no["ND-GRANTERR"]["statusText"] == "发放异常"
    assert by_no["ND-NOPRODUCT"]["title"] is None
    assert by_no["ND-NOPRODUCT"]["product"] is None
    assert by_no["ND-NOPRODUCT"]["isTest"] is False
    assert by_no["ND-NOPRODUCT"]["line"] == "待支付 · ¥10 · 2026-11-21"


def test_list_and_detail_omit_payment_secrets(client, session, clock):
    owner = session.get(User, "test-default-free")
    owner.wechat_openid = SECRET_OPENID
    add_order(
        session,
        owner,
        clock,
        "ND-SECRET01",
        id=SECRET_ID,
        status="已发放",
        product="membership_vip",
        amount=599,
        amount_fen=59900,
        paid_at=clock,
        prepay_id=SECRET_PREPAY,
        wechat_transaction_id=SECRET_TXN,
        refund_reason=SECRET_REFUND,
        client="小程序",
    )
    listed = client.get("/api/v1/pay/orders")
    detail = client.get("/api/v1/pay/orders/ND-SECRET01")
    assert listed.status_code == 200
    assert detail.status_code == 200
    for response in (listed, detail):
        assert SECRET_PREPAY not in response.text
        assert SECRET_TXN not in response.text
        assert SECRET_REFUND not in response.text
        assert SECRET_OPENID not in response.text
        assert SECRET_ID not in response.text
        assert "test-default-free" not in response.text
    row = listed.json()[0]
    assert set(row) == LIST_KEYS
    body = detail.json()
    assert set(body) == DETAIL_KEYS
    assert body["status"] == "已发放"
    assert body["order_number"] == "ND-SECRET01"
    assert body["tier"] == "membership_vip"
    assert body["title"] == "VIP"
    assert body["membership"] == "vip"
    assert body["duration_days"] == 30
    assert body["amount_fen"] == 59900
    assert body["paid_at"] == clock.astimezone(UTC).isoformat()
    assert body["granted"] is True
    assert body["orderNo"] == "ND-SECRET01"
    assert body["line"] == "已发放 · ¥599 · 2026-11-21"
    assert body["statusText"] == "已发放"
    assert body["product"] == "membership_vip"
    assert body["isTest"] is False


def test_detail_of_another_user_or_missing_order_is_404(client, session, clock):
    owner = session.get(User, "test-default-free")
    other = User(id="hidden-orders", created_at=clock)
    session.add(other)
    session.commit()
    add_order(
        session,
        owner,
        clock,
        "ND-MINE-0001",
        status="待支付",
        product="membership_regular",
        tier="regular",
        amount=299,
        amount_fen=29900,
    )
    add_order(
        session,
        other,
        clock,
        "ND-THEIRS01",
        status="已发放",
        product="membership_vip",
        amount=599,
        amount_fen=59900,
        prepay_id=SECRET_PREPAY,
    )
    theirs = client.get("/api/v1/pay/orders/ND-THEIRS01")
    missing = client.get("/api/v1/pay/orders/NO-SUCH-ORDER")
    mine = client.get("/api/v1/pay/orders/ND-MINE-0001")
    assert theirs.status_code == 404
    assert missing.status_code == 404
    assert theirs.json()["detail"] == "订单不存在"
    assert missing.json()["detail"] == "订单不存在"
    assert SECRET_PREPAY not in theirs.text
    assert mine.status_code == 200
    assert mine.json()["orderNo"] == "ND-MINE-0001"
    assert mine.json()["status"] == "待支付"
    assert mine.json()["statusText"] == "待支付"
    assert mine.json()["isTest"] is False
