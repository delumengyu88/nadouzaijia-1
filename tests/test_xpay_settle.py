"""/sync and the reconciler grant WeChat orders that are paid (2) or auto-delivered (3/4)."""

from datetime import timedelta

import pytest

from nadou.db import Database
from nadou.models import Entitlement, Order, OrderIssuance, VirtualOrder
from nadou.payments import xpay
from nadou.payments.xpay_reconcile import reconcile
from tests.test_virtual_pay import buy, configure_diamond, payment, signed_event, virtual_setup

__all__ = ["virtual_setup"]


def counts(session):
    session.expire_all()
    return session.query(OrderIssuance).count(), session.query(Entitlement).count()


@pytest.mark.parametrize("status", [3, 4])
def test_sync_grants_when_wechat_already_marked_delivered(
    virtual_setup, session, monkeypatch, status
):
    """10:37 incident: WeChat auto-marks the order delivered ~1 min after payment."""
    client = virtual_setup
    configure_diamond(monkeypatch)
    number = buy(client, "membership_diamond")
    calls = []
    monkeypatch.setattr(xpay, "provider_call", lambda *a: calls.append(a) or {})
    monkeypatch.setattr(xpay, "query_order", lambda *_: payment(number, status=status, price=100))
    for _ in range(2):
        body = client.post(f"/api/v1/xpay/orders/{number}/sync").json()
        assert body["status"] == "已发放"
    assert counts(session) == (1, 1)
    assert calls == []  # already delivered on WeChat's side: no second delivery call
    session.expire_all()
    assert session.query(VirtualOrder).one().delivery_confirmed is (status == 4)
    # A late delivery push for the same order is still idempotent.
    delivered = {
        "Event": "xpay_goods_deliver_notify",
        "OpenId": "virtual-owner",
        "Env": 0,
        "OutTradeNo": number,
        "GoodsInfo": {
            "ProductId": "official-product",
            "Quantity": 1,
            "ActualPrice": 100,
            "OrigPrice": 100,
            "Attach": "membership_diamond",
        },
    }
    assert signed_event(client, delivered, monkeypatch).text == "success"
    assert counts(session) == (1, 1)


@pytest.mark.parametrize("status", [0, 1, 6])
def test_sync_does_not_grant_unpaid_orders(virtual_setup, session, monkeypatch, status):
    client = virtual_setup
    configure_diamond(monkeypatch)
    number = buy(client, "membership_diamond")
    monkeypatch.setattr(xpay, "query_order", lambda *_: payment(number, status=status, price=100))
    assert client.post(f"/api/v1/xpay/orders/{number}/sync").json()["status"] == "待支付"
    assert counts(session) == (0, 0)


def test_reconciler_grants_stale_paid_orders_once(virtual_setup, session, monkeypatch, clock):
    client = virtual_setup
    configure_diamond(monkeypatch)
    paid = buy(client, "membership_diamond")
    unpaid = buy(client, "membership_diamond")
    remote = {paid: 4, unpaid: 1}
    monkeypatch.setattr(xpay, "provider_call", lambda *_: {})
    monkeypatch.setattr(
        xpay, "query_order", lambda env, openid, no: payment(no, status=remote[no], price=100)
    )
    database = Database(engine=session.get_bind())
    later = clock + timedelta(minutes=10)
    # Too young: the client /sync and push still have time to arrive.
    assert reconcile(database, clock, apply=True)["checked"] == 0
    dry = reconcile(database, later, apply=False)
    assert dry == {"checked": 2, "paid": 1, "granted": 0, "errors": 0}
    assert counts(session) == (0, 0)
    assert reconcile(database, later, apply=True) == {
        "checked": 2,
        "paid": 1,
        "granted": 1,
        "errors": 0,
    }
    assert reconcile(database, later, apply=True)["granted"] == 0  # now 已发放, not pending
    assert counts(session) == (1, 1)
    session.expire_all()
    status = {o.order_number: o.status for o in session.query(Order)}
    assert status == {paid: "已发放", unpaid: "待支付"}


def test_reconciler_keeps_going_after_one_wechat_error(virtual_setup, session, monkeypatch, clock):
    from fastapi import HTTPException

    client = virtual_setup
    configure_diamond(monkeypatch)
    first, _second = buy(client, "membership_diamond"), buy(client, "membership_diamond")

    def query(env, openid, no):
        if no == first:
            raise HTTPException(502, "微信虚拟支付服务暂不可用")
        return payment(no, status=2, price=100)

    monkeypatch.setattr(xpay, "provider_call", lambda *_: {})
    monkeypatch.setattr(xpay, "query_order", query)
    stats = reconcile(
        Database(engine=session.get_bind()), clock + timedelta(minutes=10), apply=True
    )
    assert stats == {"checked": 2, "paid": 1, "granted": 1, "errors": 1}
    assert counts(session) == (1, 1)
