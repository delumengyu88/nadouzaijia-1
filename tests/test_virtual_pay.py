import secrets

import pytest

from nadou.auth import WeChatLogin
from nadou.config import get_settings
from nadou.models import Order, User


@pytest.fixture
def virtual_setup(client, session, monkeypatch):
    import base64

    cfg = get_settings()
    monkeypatch.setattr(cfg, "xpay_env", 0)
    monkeypatch.setattr(cfg, "xpay_offer", "offer-test")
    from pydantic import SecretStr

    monkeypatch.setattr(cfg, "xpay_live_key", SecretStr(secrets.token_hex(16)))
    monkeypatch.setattr(cfg, "xpay_push_token", SecretStr(secrets.token_hex(16)))
    monkeypatch.setattr(
        cfg,
        "xpay_push_aes",
        SecretStr(base64.b64encode(secrets.token_bytes(32)).decode().rstrip("=")),
    )
    monkeypatch.setattr(cfg, "xpay_products", {"membership_vip": "official-product"})
    user = session.get(User, "test-default-free")
    user.wechat_openid = "virtual-owner"
    session.commit()
    monkeypatch.setattr(
        WeChatLogin,
        "exchange_session",
        staticmethod(lambda _: (user.wechat_openid, secrets.token_hex(16))),
    )
    return client


def test_virtual_create_binds_account_and_keeps_secrets_private(virtual_setup, session):
    client = virtual_setup
    response = client.post(
        "/api/v1/xpay/orders", json={"tier": "membership_vip", "code": "fresh-code"}
    )
    assert response.status_code == 200
    pay = response.json()
    number = pay["order_number"]
    assert number.startswith("XV")
    assert set(pay["virtual_payment"]) == {"mode", "signData", "paySig", "signature"}
    assert pay["amount_fen"] == 59900
    assert client.get("/api/v1/xpay/orders/" + number).json()["status"] == "待支付"
    assert "virtual_payment" not in client.get("/api/v1/xpay/orders/" + number).json()
    assert (
        client.get("/api/v1/xpay/orders/" + number, headers={"Authorization": ""}).status_code
        == 401
    )
    assert session.query(Order).filter_by(order_number=number).one().status == "待支付"


def test_virtual_order_rejects_code_for_different_owner(virtual_setup, session, monkeypatch):
    monkeypatch.setattr(
        WeChatLogin, "exchange_session", staticmethod(lambda _: ("someone-else", "unused"))
    )
    result = virtual_setup.post(
        "/api/v1/xpay/orders", json={"tier": "membership_vip", "code": "different"}
    )
    assert result.status_code == 403
    assert session.query(Order).count() == 0


def signed_event(client, event, monkeypatch):
    import base64
    import hashlib
    import json
    import struct
    import time

    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from pydantic import SecretStr

    settings = get_settings()
    token = secrets.token_hex(16)
    key = secrets.token_bytes(32)
    app = "test-app"
    monkeypatch.setattr(settings, "wx_appid", app)
    monkeypatch.setattr(settings, "xpay_push_token", SecretStr(token))
    monkeypatch.setattr(
        settings, "xpay_push_aes", SecretStr(base64.b64encode(key).decode().rstrip("="))
    )
    raw = json.dumps(event).encode()
    pad = padding.PKCS7(256).padder()
    data = (
        pad.update(secrets.token_bytes(16) + struct.pack(">I", len(raw)) + raw + app.encode())
        + pad.finalize()
    )
    enc = Cipher(algorithms.AES(key), modes.CBC(key[:16])).encryptor()
    envelope = base64.b64encode(enc.update(data) + enc.finalize()).decode()
    ts = str(int(time.time()))
    nonce = secrets.token_hex(8)
    signature = hashlib.sha1("".join(sorted([token, ts, nonce, envelope])).encode()).hexdigest()
    return client.post(
        "/api/v1/xpay/notify",
        params={"timestamp": ts, "nonce": nonce, "msg_signature": signature},
        json={"Encrypt": envelope},
    )


def payment(number, *, status=2, price=59900):
    return {
        "order_id": number,
        "order_type": 0,
        "status": status,
        "env_type": 1,
        "order_fee": price,
        "paid_fee": price,
        "wx_order_id": "platform-" + number,
        "left_fee": 0 if status == 5 else price,
    }


def buy(client):
    result = client.post("/api/v1/xpay/orders", json={"tier": "membership_vip", "code": "fresh"})
    assert result.status_code == 200, result.text
    return result.json()["order_number"]


def test_encrypted_delivery_and_confirmed_full_refund_are_idempotent(
    virtual_setup, session, monkeypatch
):
    from nadou.models import Entitlement, OrderIssuance
    from nadou.payments import xpay

    client = virtual_setup
    number = buy(client)
    remote = payment(number)
    refund_id = "refund-" + number
    wxrefund = "wx-" + refund_id
    refund = {
        "order_id": refund_id,
        "wx_order_id": wxrefund,
        "order_type": 1,
        "env_type": 1,
        "refund_fee": 59900,
        "paid_time": 100,
        "status": 8,
    }

    def query(env, openid, merchant):
        assert env == 0 and openid == "virtual-owner"
        return remote if merchant == number else refund

    monkeypatch.setattr(xpay, "query_order", query)
    delivered = {
        "Event": "xpay_goods_deliver_notify",
        "OpenId": "virtual-owner",
        "Env": 0,
        "OutTradeNo": number,
        "GoodsInfo": {
            "ProductId": "official-product",
            "Quantity": 1,
            "ActualPrice": 59900,
            "OrigPrice": 59900,
            "Attach": "membership_vip",
        },
    }
    assert signed_event(client, {**delivered, "Env": 1}, monkeypatch).status_code == 409
    assert session.query(Entitlement).count() == 0
    for _ in range(2):
        response = signed_event(client, delivered, monkeypatch)
        assert response.status_code == 200 and response.text == "success"
    assert session.query(OrderIssuance).count() == 1 and session.query(Entitlement).count() == 1
    assert client.get("/api/v1/xpay/orders/" + number).json()["status"] == "已发放"
    refund_event = {
        "Event": "xpay_refund_notify",
        "RetCode": 0,
        "OpenId": "virtual-owner",
        "MchOrderId": number,
        "WxOrderId": remote["wx_order_id"],
        "WxRefundId": wxrefund,
        "MchRefundId": refund_id,
        "RefundFee": 59900,
    }
    assert signed_event(client, refund_event, monkeypatch).status_code == 409
    assert client.get("/api/v1/xpay/orders/" + number).json()["status"] == "已发放"
    remote.update(payment(number, status=5))
    original_expiry = session.query(Entitlement).one().expires_at
    assert signed_event(client, {**refund_event, "RetCode": 1}, monkeypatch).text == "success"
    for status in (None, 1, 5, 7):
        if status is None:
            refund.pop("status")
        else:
            refund["status"] = status
        assert signed_event(client, refund_event, monkeypatch).status_code == 409
        assert client.get("/api/v1/xpay/orders/" + number).json()["status"] == "已发放"
        session.expire_all()
        assert session.query(Entitlement).one().expires_at == original_expiry
    refund["status"] = 8
    for _ in range(2):
        assert signed_event(client, refund_event, monkeypatch).text == "success"
    assert client.get("/api/v1/xpay/orders/" + number).json()["status"] == "已退款"
    session.expire_all()
    revoked_expiry = session.query(Entitlement).one().expires_at
    assert revoked_expiry < original_expiry
    assert signed_event(client, delivered, monkeypatch).text == "success"
    assert client.get("/api/v1/xpay/orders/" + number).json()["status"] == "已退款"
    session.expire_all()
    assert session.query(Entitlement).one().expires_at == revoked_expiry
    assert session.query(OrderIssuance).count() == 1


def test_poll_only_grants_matching_settled_order_and_retries_provider_ack(
    virtual_setup, session, monkeypatch
):
    from fastapi import HTTPException

    from nadou.models import Entitlement
    from nadou.payments import xpay

    client = virtual_setup
    number = buy(client)
    remote = payment(number, status=1)
    monkeypatch.setattr(xpay, "query_order", lambda *_: remote)
    sync = "/api/v1/xpay/orders/" + number + "/sync"
    assert client.post(sync).json()["status"] == "待支付"
    remote.update(payment(number, price=1))
    assert client.post(sync).status_code == 409
    assert session.query(Entitlement).count() == 0
    remote.update(payment(number))
    calls = []

    def ack(gateway, path, body):
        calls.append(body)
        if len(calls) == 1:
            raise HTTPException(502, "上游暂不可用")
        return {}

    monkeypatch.setattr(xpay, "provider_call", ack)
    assert client.post(sync).status_code == 502
    assert client.post(sync).json()["status"] == "已发放"
    assert client.post(sync).json()["status"] == "已发放"
    assert len(calls) == 2 and session.query(Entitlement).count() == 1
    assert client.post("/api/v1/pay/orders/" + number + "/sync").status_code == 404


def test_virtual_payment_stays_closed_with_incomplete_environment(virtual_setup, monkeypatch):
    cfg = get_settings()
    monkeypatch.setattr(cfg, "xpay_env", None)
    payload = {"tier": "membership_vip", "code": "fresh-code"}
    assert virtual_setup.post("/api/v1/xpay/orders", json=payload).status_code == 503
    monkeypatch.setattr(cfg, "xpay_env", 1)
    assert virtual_setup.post("/api/v1/xpay/orders", json=payload).status_code == 503
    monkeypatch.setattr(cfg, "xpay_env", 0)
    from pydantic import SecretStr

    monkeypatch.setattr(cfg, "xpay_push_token", SecretStr(""))
    assert virtual_setup.post("/api/v1/xpay/orders", json=payload).status_code == 503


def test_virtual_purchase_signs_exact_request_payload():
    import hashlib
    import hmac
    import json

    from nadou.payments.catalog import VIP
    from nadou.payments.xpay import Xpay

    app_key, session_key = secrets.token_hex(32), secrets.token_hex(32)
    order_no = "XV" + secrets.token_hex(14)
    pay = Xpay("offer", app_key, 1).purchase(VIP, "sandbox-vip", order_no, session_key)
    data = pay["signData"]
    assert json.loads(data) == {
        "offerId": "offer",
        "buyQuantity": 1,
        "env": 1,
        "currencyType": "CNY",
        "productId": "sandbox-vip",
        "goodsPrice": 59900,
        "outTradeNo": order_no,
        "attach": VIP.code,
    }
    assert (
        pay["paySig"]
        == hmac.new(
            app_key.encode(), ("requestVirtualPayment&" + data).encode(), hashlib.sha256
        ).hexdigest()
    )
    assert (
        pay["signature"]
        == hmac.new(session_key.encode(), data.encode(), hashlib.sha256).hexdigest()
    )
    assert session_key not in repr(pay) and app_key not in repr(pay)


def test_provider_confirmation_accepts_documented_empty_success_and_signs_raw_bytes(monkeypatch):
    import hashlib
    import hmac
    import json

    import httpx
    from pydantic import SecretStr

    from nadou.payments import xpay

    cfg = get_settings()
    monkeypatch.setattr(cfg, "wx_appid", "test")
    monkeypatch.setattr(cfg, "wx_appsecret", SecretStr(secrets.token_hex(12)))
    gateway = xpay.Xpay("offer", secrets.token_hex(24), 0)
    token = secrets.token_hex(8)
    real_client = httpx.Client

    def handle(request):
        if request.url.path == "/cgi-bin/stable_token":
            return httpx.Response(200, json={"access_token": token})
        assert request.url.path == "/xpay/notify_provide_goods"
        assert request.url.params["access_token"] == token
        assert json.loads(request.content) == {"order_id": "example", "env": 0}
        signed = hmac.new(
            gateway.app_key.encode(),
            request.url.path.encode() + b"&" + request.content,
            hashlib.sha256,
        )
        assert request.url.params["pay_sig"] == signed.hexdigest()
        return httpx.Response(200)

    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handle), **kwargs),
    )
    assert (
        xpay.provider_call(gateway, "/xpay/notify_provide_goods", {"order_id": "example", "env": 0})
        == {}
    )
