"""Create a priced order, verify a WeChat notification, and grant once."""

import json
import logging
import re
import secrets
from collections.abc import Mapping
from datetime import datetime
from uuid import uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from nadou.admin.orders import EntitlementIssuer, OrderGrantWriter, OrderLookup
from nadou.config import get_settings
from nadou.models import Order, User
from nadou.payments.catalog import (
    DIAMOND,
    LIVE_TEST,
    PayProduct,
    product_by_code,
    product_for_sale,
    products_for,
)
from nadou.payments.config import (
    WeChatPaySettings,
    diamond_product_id,
    live_test_product_id,
    load_pay_settings,
    user_may_use_test_tier,
)
from nadou.payments.crypto import (
    decrypt_resource,
    mini_program_pay_sign,
    notify_message,
    verify_rsa,
)
from nadou.payments.gateway import WeChatUpstreamError, build_gateway
from nadou.user_orders import UserOrders

logger = logging.getLogger("nadou.payments")

_ORDER_NUMBER = re.compile(r"^[A-Za-z0-9_-]{6,32}$")
_MAX_NOTIFY_BODY = 65536
_TIMESTAMP_SKEW = 300


class PayError(Exception):
    def __init__(self, status_code: int, detail: str, *, notify: bool = False) -> None:
        self.status_code = status_code
        self.detail = detail
        self.notify = notify


class PaymentService:
    def __init__(self, clock, notifier, grant_writer) -> None:
        self.clock = clock
        self.notifier = notifier
        self.grant_writer = grant_writer or OrderGrantWriter()

    def list_tiers(self, user: User, *, virtual: bool = False) -> dict:
        tiers = [product.public() for product in products_for(user_may_use_test_tier(user.id))]
        if virtual and (product_id := diamond_product_id()):
            tiers.append({**DIAMOND.public(), "product_id": product_id})
        if virtual and (product_id := live_test_product_id(user.id)):
            tiers.append({**LIVE_TEST.public(), "product_id": product_id})
        return {"tiers": tiers}

    def create(self, session: Session, user: User, tier: str | None) -> dict:
        product = product_for_sale(
            (tier or "").strip(), test_enabled=user_may_use_test_tier(user.id)
        )
        if product is None:
            raise PayError(400, "档位无效")
        config = load_pay_settings(get_settings())
        if config is None:
            raise PayError(503, "支付未配置")
        if not user.wechat_openid:
            raise PayError(409, "缺少微信 openid")
        # Insert only after WeChat accepts the order. A failed prepay must not
        # leave a row, and the request session commits when the route returns.
        order = self._insert_after_prepay(session, user, product, config)
        params = self._client_pay(config, order.prepay_id or "")
        logger.info("wechat prepay created order=%s", order.order_number)
        return _view(order, product, params)

    def get_order(self, session: Session, user: User, order_number: str) -> dict:
        order = self._own(session, user, order_number)
        return _view(order, product_by_code(order.product))

    def sync(self, session: Session, user: User, order_number: str) -> dict:
        order = self._own(session, user, order_number)
        if order.order_number.startswith("XV"):
            raise PayError(404, "订单不存在")
        if order.status in {"已发放", "发放异常", "已退款"}:
            return _view(order, product_by_code(order.product))
        config = load_pay_settings(get_settings())
        if config is None:
            raise PayError(503, "支付未配置")
        try:
            txn = build_gateway(config).query(order.order_number)
        except WeChatUpstreamError as exc:
            logger.warning("wechat query failed status=%s code=%s", exc.status, exc.code)
            raise PayError(502, "微信支付查询失败") from None
        if isinstance(txn, dict) and txn.get("trade_state") == "SUCCESS":
            self._confirm(session, order, config, txn, notify=False)
        return _view(order, product_by_code(order.product))

    def handle_notify(self, session: Session, body: bytes, headers: Mapping[str, str]) -> None:
        config = load_pay_settings(get_settings())
        if config is None:
            raise PayError(503, "config", notify=True)
        txn = _decrypt_notify(config, body, headers, self.clock())
        number = txn.get("out_trade_no")
        order = (
            OrderLookup.by_order_number(session, number)
            if isinstance(number, str) and _ORDER_NUMBER.fullmatch(number)
            else None
        )
        if order is None or order.order_number.startswith("XV"):
            raise PayError(404, "order", notify=True)
        self._confirm(session, order, config, txn, notify=True)

    def _confirm(
        self,
        session: Session,
        order: Order,
        config: WeChatPaySettings,
        txn: dict,
        *,
        notify: bool,
    ) -> None:
        identity = _identity_reason(order, config, txn)
        if identity is not None:
            raise _mismatch(identity, notify)
        if txn.get("trade_state") != "SUCCESS":
            return
        OrderLookup.lock_membership(session, order)
        user = session.get(User, order.user_id)
        reason = _success_reason(order, user, txn)
        if reason is not None:
            if notify and reason in {"amount", "openid"}:
                self.notifier.notify(
                    "支付已成功但未开通",
                    f"订单 {order.order_number} 校验未通过（{reason}），未开通会员",
                )
            raise _mismatch(reason, notify)
        _apply(session, order, txn, self.clock(), self.notifier, self.grant_writer)
        logger.info("wechat payment accepted order=%s status=%s", order.order_number, order.status)

    def _insert_after_prepay(
        self, session: Session, user: User, product: PayProduct, config: WeChatPaySettings
    ) -> Order:
        now = self.clock()
        openid = user.wechat_openid or ""
        for _ in range(5):
            number = _new_order_number(now)
            try:
                prepay_id = build_gateway(config).prepay(
                    description=product.description,
                    out_trade_no=number,
                    amount_fen=product.amount_fen,
                    openid=openid,
                    notify_url=config.notify_url,
                )
            except WeChatUpstreamError as exc:
                logger.warning("wechat prepay failed status=%s code=%s", exc.status, exc.code)
                raise PayError(502, "微信支付下单失败") from None
            order = Order(
                id=str(uuid4()),
                order_number=number,
                user_id=user.id,
                tier=product.membership,
                duration_days=product.duration_days,
                amount=product.amount_yuan,
                amount_fen=product.amount_fen,
                product=product.code,
                status="待支付",
                created_at=now,
                client="小程序",
                prepay_id=prepay_id,
            )
            try:
                with session.begin_nested():
                    session.add(order)
                    session.flush()
            except IntegrityError:
                session.expunge(order)
                continue
            return order
        raise PayError(500, "订单创建失败")

    def _own(self, session: Session, user: User, order_number: str) -> Order:
        if not _ORDER_NUMBER.fullmatch(order_number or ""):
            raise PayError(404, "订单不存在")
        order = OrderLookup.by_order_number(session, order_number)
        if order is None or order.user_id != user.id:
            raise PayError(404, "订单不存在")
        return order

    def _client_pay(self, config: WeChatPaySettings, prepay_id: str) -> dict:
        timestamp = str(int(self.clock().timestamp()))
        nonce = secrets.token_hex(16)
        package = f"prepay_id={prepay_id}"
        return {
            "timeStamp": timestamp,
            "nonceStr": nonce,
            "package": package,
            "signType": "RSA",
            "paySign": mini_program_pay_sign(
                config.private_key, config.appid, timestamp, nonce, package
            ),
        }


def _new_order_number(now: datetime) -> str:
    return f"ND{now.strftime('%y%m%d%H%M%S')}{secrets.token_hex(4)}"


def _view(order: Order, product: PayProduct | None, pay: dict | None = None) -> dict:
    body = {
        "order_number": order.order_number,
        "tier": order.product,
        "title": None if product is None else product.title,
        "membership": order.tier,
        "duration_days": order.duration_days,
        "amount_fen": order.amount_fen,
        "status": order.status,
        "paid_at": order.paid_at.isoformat() if order.paid_at else None,
        "granted": order.status == "已发放",
    }
    if order.program_name:
        # One-program band unlock: permanent, so the day count is not shown.
        body["program_name"] = order.program_name
        body["permanent"] = True
    # Mini-program names. Keys above stay as they are, including Chinese status.
    body.update(UserOrders.detail_fields(order))
    if pay is not None:
        body.update(pay)
    return body


def _mismatch(reason: str, notify: bool) -> PayError:
    if notify:
        return PayError(400, reason, notify=True)
    if reason == "amount":
        return PayError(400, "金额不符")
    return PayError(400, "支付校验失败")


def _identity_reason(order: Order, config: WeChatPaySettings, txn: Mapping) -> str | None:
    if txn.get("mchid") != config.mchid:
        return "mchid"
    if txn.get("appid") != config.appid:
        return "appid"
    if txn.get("out_trade_no") != order.order_number:
        return "order"
    return None


def _success_reason(order: Order, user: User | None, txn: Mapping) -> str | None:
    amount = txn.get("amount")
    total = amount.get("total") if isinstance(amount, dict) else None
    if type(total) is not int or total != order.amount_fen:
        return "amount"
    payer = txn.get("payer")
    openid = payer.get("openid") if isinstance(payer, dict) else None
    if user is None or not user.wechat_openid or openid != user.wechat_openid:
        return "openid"
    transaction_id = txn.get("transaction_id")
    if (
        not isinstance(transaction_id, str)
        or not transaction_id.strip()
        or len(transaction_id) > 64
    ):
        return "transaction"
    if order.wechat_transaction_id and order.wechat_transaction_id != transaction_id:
        return "transaction"
    return None


def _apply(session: Session, order: Order, txn: Mapping, now: datetime, notifier, writer) -> None:
    if order.status in {"已退款", "已发放", "发放异常"}:
        return
    order.wechat_transaction_id = str(txn.get("transaction_id") or "").strip()
    if order.paid_at is None:
        order.paid_at = now
    if order.status == "待支付":
        order.status = "已支付"
    EntitlementIssuer(session, writer, notifier, now).issue(order)


def _decrypt_notify(
    config: WeChatPaySettings, body: bytes, headers: Mapping[str, str], now: datetime
) -> dict:
    if not body or len(body) > _MAX_NOTIFY_BODY:
        raise PayError(401, "signature", notify=True)
    timestamp = headers.get("Wechatpay-Timestamp") or ""
    nonce = headers.get("Wechatpay-Nonce") or ""
    signature = headers.get("Wechatpay-Signature") or ""
    serial = headers.get("Wechatpay-Serial") or ""
    if serial != config.platform_key_id or not _fresh(timestamp, now) or not _nonce_ok(nonce):
        raise PayError(401, "signature", notify=True)
    if not signature or len(signature) > 1024:
        raise PayError(401, "signature", notify=True)
    message = notify_message(timestamp, nonce, body)
    if not verify_rsa(config.platform_key, message, signature):
        raise PayError(401, "signature", notify=True)
    try:
        envelope = json.loads(body)
    except ValueError:
        raise PayError(400, "decrypt", notify=True) from None
    resource = envelope.get("resource") if isinstance(envelope, dict) else None
    if not isinstance(resource, dict) or resource.get("algorithm") != "AEAD_AES_256_GCM":
        raise PayError(400, "decrypt", notify=True)
    try:
        plain = decrypt_resource(
            config.api_v3_key,
            nonce=resource.get("nonce") or "",
            ciphertext=resource.get("ciphertext") or "",
            associated_data=resource.get("associated_data") or "",
        )
        txn = json.loads(plain)
    except (ValueError, json.JSONDecodeError):
        raise PayError(400, "decrypt", notify=True) from None
    if not isinstance(txn, dict):
        raise PayError(400, "decrypt", notify=True)
    return txn


def _fresh(timestamp: str, now: datetime) -> bool:
    if not timestamp.isdigit() or len(timestamp) > 12:
        return False
    return abs(int(now.timestamp()) - int(timestamp)) <= _TIMESTAMP_SKEW


def _nonce_ok(nonce: str) -> bool:
    return bool(nonce) and len(nonce) <= 128
