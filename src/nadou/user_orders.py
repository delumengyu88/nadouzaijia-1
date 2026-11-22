"""Signed-in mini-program order list. Reads ``orders`` and does not take payment."""

from datetime import UTC
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Header
from sqlalchemy import select
from sqlalchemy.orm import Session

from nadou.auth import UserSessions
from nadou.models import Order, User
from nadou.payments.catalog import excluded_from_workbench_stats, product_by_code


class UserOrders:
    """Own orders for the mini program, newest first.

    List ``status`` uses the mini program's English codes. ``已支付`` and
    ``已退款`` have no code there, so this class uses ``paid`` and ``refunded``.
    ``statusText`` is the Chinese value stored on the order.
    """

    BEIJING = ZoneInfo("Asia/Shanghai")
    STATUS = {
        "待支付": "pending",
        "已支付": "paid",
        "已发放": "granted",
        "发放异常": "grant_error",
        "已退款": "refunded",
    }

    @staticmethod
    def english_status(stored: str) -> str | None:
        return UserOrders.STATUS.get(stored)

    @staticmethod
    def title(order: Order) -> str | None:
        product = product_by_code(order.product)
        if product is None:
            return None
        return product.title

    @staticmethod
    def price_text(order: Order) -> str:
        if order.amount_fen is None:
            return f"¥{order.amount}"
        yuan, fen = divmod(order.amount_fen, 100)
        if fen == 0:
            return f"¥{yuan}"
        return f"¥{yuan}.{fen:02d}"

    @staticmethod
    def day(order: Order) -> str:
        created = order.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        return created.astimezone(UserOrders.BEIJING).date().isoformat()

    @staticmethod
    def line(order: Order) -> str:
        return f"{order.status} · {UserOrders.price_text(order)} · {UserOrders.day(order)}"

    @staticmethod
    def is_test(order: Order) -> bool:
        return excluded_from_workbench_stats(order.product)

    @staticmethod
    def list_row(order: Order) -> dict:
        return {
            "orderNo": order.order_number,
            "title": UserOrders.title(order),
            "line": UserOrders.line(order),
            "status": UserOrders.english_status(order.status),
            "statusText": order.status,
            "product": order.product,
            "isTest": UserOrders.is_test(order),
        }

    @staticmethod
    def detail_fields(order: Order) -> dict:
        """Names the pay query does not already return.

        ``title`` and ``status`` stay on the payment view: ``status`` there is
        still the Chinese value stored on the order.
        """

        return {
            "orderNo": order.order_number,
            "line": UserOrders.line(order),
            "statusText": order.status,
            "product": order.product,
            "isTest": UserOrders.is_test(order),
        }

    @staticmethod
    def list_for(session: Session, user: User) -> list[dict]:
        rows = session.scalars(
            select(Order)
            .where(Order.user_id == user.id)
            .order_by(Order.created_at.desc(), Order.id.desc())
        ).all()
        return [UserOrders.list_row(order) for order in rows]

    @staticmethod
    def register(app: FastAPI, get_session, clock) -> None:
        def current_user(
            authorization: str | None = Header(default=None),
            session: Session = Depends(get_session, scope="function"),
        ) -> User:
            return UserSessions.resolve(session, authorization, clock())

        @app.get("/api/v1/pay/orders")
        def list_pay_orders(
            user: User = Depends(current_user),
            session: Session = Depends(get_session, scope="function"),
        ) -> list[dict]:
            return UserOrders.list_for(session, user)
