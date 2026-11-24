"""Owned virtual-goods orders, encrypted delivery events and verified order queries."""

import secrets
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from nadou.admin.audit import AuditWriter
from nadou.admin.orders import EntitlementIssuer, OrderGrantWriter, OrderLookup, OrderRefund
from nadou.auth import WeChatLogin
from nadou.config import get_settings
from nadou.models import Order, User, VirtualOrder
from nadou.payments import xpay, xpay_push
from nadou.payments.catalog import (
    DIAMOND,
    LIVE_TEST,
    UNLOCK_PRODUCTS,
    product_by_code,
    product_for_sale,
)
from nadou.payments.config import (
    diamond_product_id,
    live_test_product_id,
    live_virtual_product_id,
    user_may_use_test_tier,
)
from nadou.payments.notify_io import notify_body
from nadou.payments.service import _view
from nadou.payments.summary import require_context_product
from nadou.payments.unlock import is_unlock, unlock_offer
from nadou.user_orders import UserOrders


class CreateVirtualOrder(BaseModel):
    tier: str = Field(min_length=1, max_length=32)
    code: str = Field(min_length=1, max_length=256, repr=False)
    program_name: str | None = Field(default=None, min_length=1, max_length=200)


def virtual_row(session, number, user_id=None):
    stmt = (
        select(Order, VirtualOrder)
        .join(VirtualOrder, VirtualOrder.order_id == Order.id)
        .where(Order.order_number == number)
    )
    if user_id is not None:
        stmt = stmt.where(Order.user_id == user_id)
    pair = session.execute(stmt).first()
    if pair is None:
        raise HTTPException(404, "订单不存在")
    return pair


def checked_payment(order, virtual, remote):
    if (
        remote.get("order_id") != order.order_number
        or remote.get("order_type") not in (0, 7)
        or remote.get("env_type") != virtual.env + 1
    ):
        raise HTTPException(409, "虚拟支付订单信息不匹配")
    if remote.get("status") in (0, 1, 6):
        return
    if any(
        type(remote.get(k)) is not int or remote[k] != order.amount_fen
        for k in ("order_fee", "paid_fee")
    ):
        raise HTTPException(409, "虚拟支付订单金额不匹配")
    reference = remote.get("wx_order_id")
    if (
        not isinstance(reference, str)
        or not 0 < len(reference) <= 64
        or order.wechat_transaction_id not in (None, reference)
    ):
        raise HTTPException(409, "虚拟支付交易信息不匹配")


PAID_STATUSES = (2, 3, 4)


def lock(session, order, virtual):
    OrderLookup.lock_membership(session, order)
    session.refresh(virtual)


def grant(session, order, virtual, remote, now, notifier, writer):
    checked_payment(order, virtual, remote)
    if order.status == "已退款":
        return
    if order.status in ("已发放", "发放异常"):
        return
    order.wechat_transaction_id = remote["wx_order_id"]
    order.paid_at = order.paid_at or now
    order.status = "已支付"
    EntitlementIssuer(session, writer or OrderGrantWriter(), notifier, now).issue(order)


def settle(session, order, virtual, remote, now, notifier, writer):
    """Grant from a verified WeChat order query. Shared by /sync and the reconciler.

    Call without holding locks (``remote`` is fetched first); this takes the owner lock.
    """
    lock(session, order, virtual)
    checked_payment(order, virtual, remote)
    # 2 = paid, awaiting delivery; 3/4 = WeChat is delivering / auto-marked delivered
    # (about a minute after payment). All three are paid. grant() is idempotent.
    if remote.get("status") in PAID_STATUSES:
        grant(session, order, virtual, remote, now, notifier, writer)
    if order.status == "已发放" and not virtual.delivery_confirmed:
        # Persist the entitlement (and release the lock) before calling WeChat.
        session.commit()
        if remote.get("status") == 2:
            xpay.provider_call(
                xpay.configured(virtual.env),
                "/xpay/notify_provide_goods",
                {"order_id": order.order_number, "env": virtual.env},
            )
            virtual.delivery_confirmed = True
        elif remote.get("status") == 4:
            virtual.delivery_confirmed = True


def handle_delivery(session, order, virtual, event, remote, clock, notifier, writer):
    goods = event.get("GoodsInfo")
    if (
        event.get("Env") != virtual.env
        or not isinstance(goods, dict)
        or goods.get("ProductId") != virtual.product_id
        or type(goods.get("Quantity")) is not int
        or goods["Quantity"] != 1
        or goods.get("ActualPrice") != order.amount_fen
        or goods.get("OrigPrice") != order.amount_fen
        or goods.get("Attach") != order.product
    ):
        raise HTTPException(409, "虚拟支付道具不匹配")
    checked_payment(order, virtual, remote)
    if order.status == "已退款":
        return
    if remote.get("status") not in PAID_STATUSES:
        raise HTTPException(409, "支付状态待核实")
    grant(session, order, virtual, remote, clock(), notifier, writer)
    if order.status != "已发放":
        session.commit()
        raise HTTPException(503, "权益发放待处理")
    virtual.delivery_confirmed = True


def handle_refund(session, order, virtual, event, remote, refund, now):
    if event.get("RetCode") != 0:
        return
    checked_payment(order, virtual, remote)
    expected_fee = order.amount_fen
    if (
        not event.get("WxRefundId")
        or not event.get("MchRefundId")
        or event.get("WxOrderId") != remote.get("wx_order_id")
        or refund.get("order_id") != event["MchRefundId"]
        or refund.get("wx_order_id") != event["WxRefundId"]
        # WeChat status 8 confirms receipt by the user on the refund order.
        # Status 5 confirms only the merchant-side debit.
        or refund.get("status") != 8
        or refund.get("order_type") not in (1, 8)
        or refund.get("env_type") != virtual.env + 1
        or not isinstance(refund.get("paid_time"), int)
        or refund["paid_time"] <= 0
        or remote.get("left_fee") != 0
        or remote.get("status") not in (5, 8)
        or refund.get("refund_fee") != expected_fee
        or event.get("RefundFee") != expected_fee
    ):
        raise HTTPException(409, "退款金额或平台状态待核实")
    if virtual.wx_refund_no is not None:
        if (virtual.wx_refund_no, virtual.refund_no) != (event["WxRefundId"], event["MchRefundId"]):
            raise HTTPException(409, "退款记录不匹配")
        return
    if order.status == "已发放":
        OrderRefund(session, AuditWriter(session, now, None, None), now).refund(
            order, "微信虚拟支付整单退款"
        )
    elif order.status != "已退款":
        order.status = "已退款"
        order.refunded_at = now
        order.refund_reason = "微信虚拟支付整单退款"
    order.wechat_transaction_id = remote["wx_order_id"]
    virtual.wx_refund_no = event["WxRefundId"]
    virtual.refund_no = event["MchRefundId"]


def register_virtual_orders(app: FastAPI, get_session, payer, clock, notifier, writer):
    @app.post("/api/v1/xpay/orders")
    def create(
        payload: CreateVirtualOrder,
        user: User = Depends(payer),
        session: Session = Depends(get_session, scope="function"),
    ):
        unlock_program = None
        if is_unlock(payload.tier):
            offer = unlock_offer(session, user, payload.program_name, payload.tier, clock())
            unlock_program = offer["program_name"]
        else:
            require_context_product(
                session, user, payload.program_name, payload.tier, clock(), virtual=True
            )
        gateway = xpay.configured()
        xpay_push.configured_key()
        test_allowed = user_may_use_test_tier(user.id)
        if unlock_program is not None:
            product = UNLOCK_PRODUCTS[payload.tier]
            product_id = live_virtual_product_id(product.code)
            if not product_id:
                raise HTTPException(503, "单节目解锁暂未开放支付")
        elif payload.tier == DIAMOND.code:
            product = DIAMOND
            product_id = diamond_product_id()
            if not product_id:
                raise HTTPException(503, "体验会员暂未开放支付")
        elif payload.tier == LIVE_TEST.code:
            product = LIVE_TEST
            product_id = live_test_product_id(user.id)
            if not product_id:
                raise HTTPException(400, "档位无效")
        else:
            product = product_for_sale(payload.tier, test_enabled=test_allowed)
            if not product:
                raise HTTPException(400, "档位无效")
            product_id = get_settings().xpay_products.get(product.code)
        if gateway.env == 1 and (not test_allowed or not product.test_only):
            raise HTTPException(403, "沙箱仅支持测试账号和测试档位")
        if not product_id or len(product_id) > 128:
            raise HTTPException(503, "虚拟支付道具未配置")
        openid, key = WeChatLogin.exchange_session(payload.code)
        if not user.wechat_openid or openid != user.wechat_openid:
            raise HTTPException(403, "登录账号不匹配")
        order = Order(
            id=str(uuid4()),
            order_number="XV" + secrets.token_hex(14),
            user_id=user.id,
            tier=product.membership,
            duration_days=product.duration_days,
            amount=product.amount_yuan,
            amount_fen=product.amount_fen,
            product=product.code,
            status="待支付",
            created_at=clock(),
            client="小程序",
            program_name=unlock_program,
        )
        session.add(order)
        session.flush()
        virtual = VirtualOrder(order_id=order.id, env=gateway.env, product_id=product_id)
        session.add(virtual)
        body = _view(order, product)
        body["virtual_payment"] = gateway.purchase(product, product_id, order.order_number, key)
        return _private(body)

    @app.get("/api/v1/xpay/orders/{number}")
    def get(
        number: str,
        user: User = Depends(payer),
        session: Session = Depends(get_session, scope="function"),
    ):
        order, _ = virtual_row(session, number, user.id)
        return UserOrders.private_response(_view(order, product_by_code(order.product)))

    @app.post("/api/v1/xpay/orders/{number}/sync")
    def sync(
        number: str,
        user: User = Depends(payer),
        session: Session = Depends(get_session, scope="function"),
    ):
        order, virtual = virtual_row(session, number, user.id)
        if order.status == "已退款" or virtual.delivery_confirmed:
            return _private(_view(order, product_by_code(order.product)))
        remote = xpay.query_order(virtual.env, user.wechat_openid, number)
        settle(session, order, virtual, remote, clock(), notifier, writer)
        # Release the owner-row lock here, not in dependency teardown after the response.
        session.commit()
        return _private(_view(order, product_by_code(order.product)))

    @app.get("/api/v1/xpay/notify")
    def challenge(request: Request):
        xpay_push.verify(request.query_params)
        return PlainTextResponse(request.query_params.get("echostr", ""))

    @app.post("/api/v1/xpay/notify")
    def notify(
        request: Request,
        body: bytes = Depends(notify_body),
        session: Session = Depends(get_session, scope="function"),
    ):
        # Sync HTTP and row-lock waits MUST stay off the ASGI event loop. A
        # competing request needs that loop to schedule its commit/lock release.
        event = xpay_push.open_event(request.query_params, body)
        kind = event.get("Event")
        if kind not in ("xpay_goods_deliver_notify", "xpay_refund_notify"):
            raise HTTPException(400, "不支持的通知类型")
        if kind == "xpay_refund_notify" and type(event.get("RetCode")) is not int:
            raise HTTPException(400, "缺少退款结果")
        if kind == "xpay_refund_notify" and event["RetCode"] != 0:
            return PlainTextResponse("success")
        number = event.get("OutTradeNo" if kind == "xpay_goods_deliver_notify" else "MchOrderId")
        if not isinstance(number, str) or not 8 <= len(number) <= 32:
            raise HTTPException(400, "缺少订单号")
        order, virtual = virtual_row(session, number)
        owner = session.get(User, order.user_id)
        if not owner.wechat_openid or event.get("OpenId") != owner.wechat_openid:
            raise HTTPException(409, "通知账号不匹配")
        remote = xpay.query_order(virtual.env, owner.wechat_openid, number)
        refund = None
        if kind == "xpay_refund_notify":
            if not all(
                isinstance(event.get(k), str) and 0 < len(event[k]) <= 64
                for k in ("MchRefundId", "WxRefundId")
            ):
                raise HTTPException(400, "缺少退款单号")
            refund = xpay.query_order(virtual.env, owner.wechat_openid, event["MchRefundId"])
        lock(session, order, virtual)
        if kind == "xpay_goods_deliver_notify":
            handle_delivery(session, order, virtual, event, remote, clock, notifier, writer)
        else:
            handle_refund(session, order, virtual, event, remote, refund, clock())
        session.commit()
        return PlainTextResponse("success")


def _private(body):
    from fastapi.responses import JSONResponse

    return JSONResponse(body, headers={"Cache-Control": "no-store"})
