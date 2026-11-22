"""WeChat Pay JSAPI: server price, verified notify, no network."""

import base64
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from nadou.auth import UserSessions
from nadou.config import get_settings
from nadou.db import Database
from nadou.main import create_app
from nadou.models import Entitlement, Order, OrderIssuance, User
from nadou.payments.config import load_pay_settings
from nadou.payments.crypto import load_public_key, notify_message, verify_rsa
from nadou.payments.gateway import HttpxWeChatPayGateway, WeChatUpstreamError

API_V3_KEY = "0123456789abcdef0123456789abcdef"
APPID = "wx-test-app"
MCH_ID = "mch-test"
MCH_SERIAL = "TESTSERIAL"
PLATFORM_ID = "PUB_KEY_ID_TEST"
NOTIFY_URL = "https://pay.example.test/api/v1/pay/wechat/notify"
MARKER = "BANK-MARKER-DO-NOT-LOG"
AES_NONCE = "nonce12bytes"


@pytest.fixture(autouse=True)
def no_wechat_network(monkeypatch):
    def blocked(*_args, **_kwargs):
        raise AssertionError("payment test called the network")

    monkeypatch.setattr(httpx, "request", blocked)
    monkeypatch.setattr(httpx, "get", blocked)
    monkeypatch.setattr(httpx, "post", blocked)


class FakeGateway:
    def __init__(self) -> None:
        self.prepay_calls = []
        self.queries = []
        self.prepay_id = "wx_prepay_test"
        self.fail = False
        self.query_result = {"trade_state": "NOTPAY"}

    def prepay(self, **kwargs):
        self.prepay_calls.append(kwargs)
        if self.fail:
            raise WeChatUpstreamError(500, "SYSTEM_ERROR")
        return self.prepay_id

    def query(self, out_trade_no):
        self.queries.append(out_trade_no)
        return self.query_result


@pytest.fixture
def pay_keys(tmp_path, monkeypatch):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    key_path = tmp_path / "mch.pem"
    pub_path = tmp_path / "wx.pem"
    key_path.write_bytes(
        private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    pub_path.write_bytes(
        private_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    monkeypatch.setenv("NADOU_WX_APPID", APPID)
    monkeypatch.setenv("NADOU_WECHAT_MCH_ID", MCH_ID)
    monkeypatch.setenv("NADOU_WECHAT_API_V3_KEY", API_V3_KEY)
    monkeypatch.setenv("NADOU_WECHAT_MCH_PRIVATE_KEY_PATH", str(key_path))
    monkeypatch.setenv("NADOU_WECHAT_MCH_CERT_SERIAL", MCH_SERIAL)
    monkeypatch.setenv("NADOU_WECHAT_PAY_PUBLIC_KEY_PATH", str(pub_path))
    monkeypatch.setenv("NADOU_WECHAT_PAY_PUBLIC_KEY_ID", PLATFORM_ID)
    monkeypatch.setenv("NADOU_WECHAT_NOTIFY_URL", NOTIFY_URL)
    monkeypatch.setenv("NADOU_WECHAT_PAY_TEST_TIER", "0")
    get_settings.cache_clear()
    yield private_key
    get_settings.cache_clear()


@pytest.fixture
def gateway():
    return FakeGateway()


@pytest.fixture
def pay_app(session, clock, pay_keys, gateway, monkeypatch):
    del pay_keys
    monkeypatch.setattr("nadou.payments.service.build_gateway", lambda _config: gateway)
    return TestClient(create_app(database=Database(engine=session.get_bind()), now=lambda: clock))


def login(session, clock, openid="openid-pay"):
    user = User(id=str(uuid4()), created_at=clock, wechat_openid=openid)
    session.add(user)
    session.flush()
    token = UserSessions.issue(session, user, clock)
    session.commit()
    return user, {"Authorization": "Bearer " + token}


def refresh(session):
    session.commit()
    session.expire_all()


def grant_count(session, user_id):
    refresh(session)
    return session.scalar(
        select(func.count()).select_from(Entitlement).where(Entitlement.user_id == user_id)
    )


def order_row(session, number):
    refresh(session)
    return session.scalar(select(Order).where(Order.order_number == number))


def encrypt_resource(txn):
    plain = json.dumps(txn, ensure_ascii=False, separators=(",", ":")).encode()
    raw = AESGCM(API_V3_KEY.encode()).encrypt(AES_NONCE.encode(), plain, b"transaction")
    return base64.b64encode(raw).decode()


def notify_body(txn):
    envelope = {
        "id": "notify-test",
        "create_time": "2026-11-21T12:00:00+08:00",
        "resource_type": "encrypt-resource",
        "event_type": "TRANSACTION.SUCCESS",
        "summary": "支付成功",
        "resource": {
            "original_type": "transaction",
            "algorithm": "AEAD_AES_256_GCM",
            "ciphertext": encrypt_resource(txn),
            "associated_data": "transaction",
            "nonce": AES_NONCE,
        },
    }
    return json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode()


def sign_notify(private_key, body, timestamp, nonce="notify-nonce"):
    message = notify_message(timestamp, nonce, body)
    signature = base64.b64encode(
        private_key.sign(message, padding.PKCS1v15(), hashes.SHA256())
    ).decode()
    return {
        "Wechatpay-Timestamp": timestamp,
        "Wechatpay-Nonce": nonce,
        "Wechatpay-Signature": signature,
        "Wechatpay-Serial": PLATFORM_ID,
        "Content-Type": "application/json",
    }


def transaction(order_number, **overrides):
    body = {
        "mchid": MCH_ID,
        "appid": APPID,
        "out_trade_no": order_number,
        "transaction_id": "tx-" + order_number,
        "trade_type": "JSAPI",
        "trade_state": "SUCCESS",
        "trade_state_desc": "支付成功",
        "bank_type": MARKER,
        "success_time": "2026-11-21T12:00:00+08:00",
        "payer": {"openid": "openid-pay"},
        "amount": {"total": 59900, "payer_total": 59900, "currency": "CNY"},
    }
    body.update(overrides)
    return body


def post_notify(client, private_key, clock, txn, *, signature=None, timestamp=None):
    body = notify_body(txn)
    stamp = str(int(clock.timestamp())) if timestamp is None else timestamp
    headers = sign_notify(private_key, body, stamp)
    if signature is not None:
        headers["Wechatpay-Signature"] = signature
    return client.post("/api/v1/pay/wechat/notify", content=body, headers=headers), body


def test_create_ignores_client_amount_and_signs_request_payment(
    pay_app, gateway, session, clock, pay_keys
):
    _user, headers = login(session, clock)
    response = pay_app.post(
        "/api/v1/pay/orders",
        headers=headers,
        json={"tier": "membership_vip", "amount": 1},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tier"] == "membership_vip"
    assert body["membership"] == "vip"
    assert body["duration_days"] == 30
    assert body["amount_fen"] == 59900
    assert body["status"] == "待支付"
    assert body["granted"] is False
    assert body["signType"] == "RSA"
    assert body["package"] == "prepay_id=wx_prepay_test"
    assert API_V3_KEY not in response.text
    assert "PRIVATE KEY" not in response.text
    message = f"{APPID}\n{body['timeStamp']}\n{body['nonceStr']}\n{body['package']}\n".encode()
    assert verify_rsa(pay_keys.public_key(), message, body["paySign"])
    assert gateway.prepay_calls == [
        {
            "description": "VIP 30 天",
            "out_trade_no": body["order_number"],
            "amount_fen": 59900,
            "openid": "openid-pay",
            "notify_url": NOTIFY_URL,
        }
    ]
    stored = order_row(session, body["order_number"])
    assert stored.amount == 599
    assert stored.amount_fen == 59900
    assert stored.tier == "vip"


def test_bad_signature_does_not_grant(pay_app, session, clock, pay_keys, caplog):
    user, headers = login(session, clock)
    created = pay_app.post(
        "/api/v1/pay/orders", headers=headers, json={"tier": "membership_vip", "amount": 1}
    ).json()
    txn = transaction(created["order_number"])
    caplog.set_level("INFO", logger="nadou.payments")
    response, _body = post_notify(pay_app, pay_keys, clock, txn, signature="not-a-valid-signature")
    assert response.status_code == 401
    assert response.json() == {"code": "FAIL", "message": "失败"}
    assert MARKER not in response.text
    assert API_V3_KEY not in caplog.text and MARKER not in caplog.text
    assert grant_count(session, user.id) == 0
    stored = order_row(session, created["order_number"])
    assert stored.status == "待支付"
    assert stored.paid_at is None
    assert stored.wechat_transaction_id is None


def test_amount_mismatch_does_not_grant(pay_app, session, clock, pay_keys, caplog):
    user, headers = login(session, clock)
    created = pay_app.post(
        "/api/v1/pay/orders", headers=headers, json={"tier": "membership_vip"}
    ).json()
    txn = transaction(created["order_number"], amount={"total": 1, "currency": "CNY"})
    caplog.set_level("INFO", logger="nadou.payments")
    response, _body = post_notify(pay_app, pay_keys, clock, txn)
    assert response.status_code == 400
    assert response.json()["code"] == "FAIL"
    assert MARKER not in response.text and MARKER not in caplog.text
    assert API_V3_KEY not in caplog.text
    assert grant_count(session, user.id) == 0
    assert order_row(session, created["order_number"]).status == "待支付"


def test_duplicate_notify_grants_once_and_auth_me_shows_vip(pay_app, session, clock, pay_keys):
    user, headers = login(session, clock)
    created = pay_app.post(
        "/api/v1/pay/orders", headers=headers, json={"tier": "membership_vip"}
    ).json()
    txn = transaction(created["order_number"])
    first, _body = post_notify(pay_app, pay_keys, clock, txn)
    second, _body = post_notify(pay_app, pay_keys, clock, txn)
    assert first.status_code == 204
    assert second.status_code == 204
    assert first.content == b""
    assert grant_count(session, user.id) == 1
    entitlement = session.scalar(select(Entitlement).where(Entitlement.user_id == user.id))
    assert entitlement.kind == "vip"
    assert entitlement.source == "order"
    assert entitlement.expires_at == clock + timedelta(days=30)
    stored = order_row(session, created["order_number"])
    issuance = session.scalar(select(OrderIssuance).where(OrderIssuance.order_id == stored.id))
    assert issuance.attempts == 1
    assert issuance.status == "issued"
    assert stored.status == "已发放"
    assert stored.wechat_transaction_id == "tx-" + created["order_number"]
    me = pay_app.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["tier"] == "vip"
    assert me.json()["vip_expires_at"] == entitlement.expires_at.isoformat()


@pytest.mark.parametrize(
    "field,value",
    [
        ("mchid", "mch-other"),
        ("appid", "wx-other"),
        ("payer", {"openid": "openid-other"}),
    ],
)
def test_merchant_app_and_openid_mismatch_do_not_grant(
    pay_app, session, clock, pay_keys, field, value
):
    user, headers = login(session, clock)
    created = pay_app.post(
        "/api/v1/pay/orders", headers=headers, json={"tier": "membership_regular"}
    ).json()
    txn = transaction(created["order_number"], amount={"total": 29900, "currency": "CNY"})
    txn[field] = value
    response, _body = post_notify(pay_app, pay_keys, clock, txn)
    assert response.status_code == 400
    assert response.json()["code"] == "FAIL"
    assert grant_count(session, user.id) == 0
    assert order_row(session, created["order_number"]).status == "待支付"


def test_non_success_trade_state_does_not_grant(pay_app, session, clock, pay_keys):
    user, headers = login(session, clock)
    created = pay_app.post(
        "/api/v1/pay/orders", headers=headers, json={"tier": "membership_vip"}
    ).json()
    txn = transaction(created["order_number"], trade_state="NOTPAY")
    response, _body = post_notify(pay_app, pay_keys, clock, txn)
    assert response.status_code == 204
    assert grant_count(session, user.id) == 0
    assert order_row(session, created["order_number"]).status == "待支付"


def test_stale_or_unknown_serial_is_rejected(pay_app, session, clock, pay_keys):
    user, headers = login(session, clock)
    created = pay_app.post(
        "/api/v1/pay/orders", headers=headers, json={"tier": "membership_vip"}
    ).json()
    txn = transaction(created["order_number"])
    stale, _body = post_notify(pay_app, pay_keys, clock, txn, timestamp="1")
    assert stale.status_code == 401
    body = notify_body(txn)
    headers_notify = sign_notify(pay_keys, body, str(int(clock.timestamp())))
    headers_notify["Wechatpay-Serial"] = "PUB_KEY_ID_OTHER"
    wrong_serial = pay_app.post("/api/v1/pay/wechat/notify", content=body, headers=headers_notify)
    assert wrong_serial.status_code == 401
    assert grant_count(session, user.id) == 0


def test_test_tier_is_hidden_and_rejected_until_the_switch_is_on(
    pay_app, gateway, session, clock, monkeypatch
):
    _user, headers = login(session, clock)
    listed = pay_app.get("/api/v1/pay/tiers", headers=headers)
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["tiers"]] == [
        "membership_regular",
        "membership_vip",
    ]
    assert listed.json()["tiers"][0]["amount_fen"] == 29900
    assert listed.json()["tiers"][1]["amount_fen"] == 59900
    rejected = pay_app.post(
        "/api/v1/pay/orders", headers=headers, json={"tier": "membership_test", "amount": 1}
    )
    assert rejected.status_code == 400
    assert rejected.json() == {"detail": "档位无效"}
    assert gateway.prepay_calls == []

    monkeypatch.setenv("NADOU_WECHAT_PAY_TEST_TIER", "1")
    get_settings.cache_clear()
    opened = pay_app.get("/api/v1/pay/tiers", headers=headers).json()
    assert opened["tiers"][-1]["id"] == "membership_test"
    assert opened["tiers"][-1]["amount_fen"] == 1
    assert opened["tiers"][-1]["amount_yuan"] == "0.01"
    created = pay_app.post(
        "/api/v1/pay/orders", headers=headers, json={"tier": "membership_test", "amount": 59900}
    )
    assert created.status_code == 200, created.text
    assert created.json()["amount_fen"] == 1
    assert created.json()["membership"] == "regular"
    assert created.json()["duration_days"] == 1
    stored = order_row(session, created.json()["order_number"])
    assert stored.amount == 0
    assert stored.amount_fen == 1
    assert gateway.prepay_calls[-1]["amount_fen"] == 1


def test_missing_config_returns_unconfigured_and_creates_no_order(
    pay_app, session, clock, monkeypatch, tmp_path
):
    _user, headers = login(session, clock)
    monkeypatch.setenv("NADOU_WECHAT_MCH_ID", "")
    get_settings.cache_clear()
    missing = pay_app.post("/api/v1/pay/orders", headers=headers, json={"tier": "membership_vip"})
    assert missing.status_code == 503
    assert missing.json() == {"detail": "支付未配置"}
    monkeypatch.setenv("NADOU_WECHAT_MCH_ID", MCH_ID)
    monkeypatch.setenv("NADOU_WECHAT_MCH_PRIVATE_KEY_PATH", str(tmp_path / "missing.pem"))
    get_settings.cache_clear()
    unread = pay_app.post(
        "/api/v1/pay/orders", headers=headers, json={"tier": "membership_regular"}
    )
    assert unread.status_code == 503
    assert unread.json()["detail"] == "支付未配置"
    refresh(session)
    assert session.scalar(select(func.count()).select_from(Order)) == 0


def test_anonymous_cannot_order_or_list_tiers(pay_app, session):
    listed = pay_app.get("/api/v1/pay/tiers")
    ordered = pay_app.post("/api/v1/pay/orders", json={"tier": "membership_vip", "amount": 1})
    assert listed.status_code == 401
    assert ordered.status_code == 401
    assert listed.json()["detail"] == "未登录"
    assert ordered.json()["detail"] == "未登录"
    refresh(session)
    assert session.scalar(select(func.count()).select_from(Order)) == 0


def test_unknown_tier_is_invalid(pay_app, session, clock):
    _user, headers = login(session, clock)
    response = pay_app.post("/api/v1/pay/orders", headers=headers, json={"tier": "nope"})
    assert response.status_code == 400
    assert response.json() == {"detail": "档位无效"}


def test_upstream_failure_rolls_back_the_order(pay_app, gateway, session, clock):
    _user, headers = login(session, clock)
    gateway.fail = True
    response = pay_app.post("/api/v1/pay/orders", headers=headers, json={"tier": "membership_vip"})
    assert response.status_code == 502
    assert response.json() == {"detail": "微信支付下单失败"}
    refresh(session)
    assert session.scalar(select(func.count()).select_from(Order)) == 0


def test_sync_grants_only_a_successful_wechat_query(pay_app, gateway, session, clock):
    user, headers = login(session, clock)
    created = pay_app.post(
        "/api/v1/pay/orders", headers=headers, json={"tier": "membership_regular", "amount": 1}
    ).json()
    number = created["order_number"]
    premature = pay_app.post(
        f"/api/v1/pay/orders/{number}/sync", headers=headers, json={"paid": True, "amount": 1}
    )
    assert premature.status_code == 200
    assert premature.json()["granted"] is False
    assert premature.json()["status"] == "待支付"
    assert grant_count(session, user.id) == 0
    gateway.query_result = transaction(number, amount={"total": 29900, "currency": "CNY"})
    confirmed = pay_app.post(f"/api/v1/pay/orders/{number}/sync", headers=headers)
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["granted"] is True
    assert confirmed.json()["status"] == "已发放"
    again = pay_app.post(f"/api/v1/pay/orders/{number}/sync", headers=headers)
    assert again.json()["granted"] is True
    assert grant_count(session, user.id) == 1
    other, other_headers = login(session, clock, openid="openid-other")
    hidden = pay_app.get(f"/api/v1/pay/orders/{number}", headers=other_headers)
    assert hidden.status_code == 404
    assert hidden.json()["detail"] == "订单不存在"
    assert grant_count(session, other.id) == 0
    mismatch = transaction(number, amount={"total": 100, "currency": "CNY"})
    second = pay_app.post(
        "/api/v1/pay/orders", headers=headers, json={"tier": "membership_regular"}
    ).json()
    gateway.query_result = mismatch | {"out_trade_no": second["order_number"]}
    rejected = pay_app.post(f"/api/v1/pay/orders/{second['order_number']}/sync", headers=headers)
    assert rejected.status_code == 400
    assert rejected.json() == {"detail": "金额不符"}
    assert grant_count(session, user.id) == 1


def test_platform_certificate_verifies_and_gateway_signs_the_fen_amount(pay_keys):
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(pay_keys.public_key())
        .serial_number(1)
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=1))
        .sign(pay_keys, hashes.SHA256())
    )
    loaded = load_public_key(certificate.public_bytes(serialization.Encoding.PEM))
    message = b"certificate-check"
    signature = base64.b64encode(
        pay_keys.sign(message, padding.PKCS1v15(), hashes.SHA256())
    ).decode()
    assert verify_rsa(loaded, message, signature)

    config = load_pay_settings(get_settings())
    assert config is not None
    captured = {}

    def transport(method, url, headers, content):
        captured["method"] = method
        captured["url"] = url
        captured["headers"] = headers
        captured["content"] = content
        return 200, b'{"prepay_id":"wx_from_transport"}'

    prepay_id = HttpxWeChatPayGateway(config, transport=transport, clock=lambda: 1700000000).prepay(
        description="VIP 30 天",
        out_trade_no="ND260101120000abcdef12",
        amount_fen=59900,
        openid="openid-pay",
        notify_url=NOTIFY_URL,
    )
    assert prepay_id == "wx_from_transport"
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/v3/pay/transactions/jsapi")
    assert captured["headers"]["Wechatpay-Serial"] == PLATFORM_ID
    assert f'serial_no="{MCH_SERIAL}"' in captured["headers"]["Authorization"]
    payload = json.loads(captured["content"])
    assert payload["amount"] == {"total": 59900, "currency": "CNY"}
    assert payload["payer"] == {"openid": "openid-pay"}
    assert payload["notify_url"] == NOTIFY_URL
    assert payload["appid"] == APPID
    assert payload["mchid"] == MCH_ID
    authorization = captured["headers"]["Authorization"]
    signature = authorization.split('signature="', 1)[1].split('"', 1)[0]
    timestamp = authorization.split('timestamp="', 1)[1].split('"', 1)[0]
    nonce = authorization.split('nonce_str="', 1)[1].split('"', 1)[0]
    signed = (
        f"POST\n/v3/pay/transactions/jsapi\n{timestamp}\n{nonce}\n".encode()
        + captured["content"]
        + b"\n"
    )
    assert verify_rsa(pay_keys.public_key(), signed, signature)
    assert API_V3_KEY not in captured["headers"]["Authorization"]


def test_pay_settings_reject_a_non_https_notify_url(pay_keys, monkeypatch):
    del pay_keys
    monkeypatch.setenv(
        "NADOU_WECHAT_NOTIFY_URL", "http://pay.example.test/api/v1/pay/wechat/notify"
    )
    get_settings.cache_clear()
    assert load_pay_settings(get_settings()) is None
