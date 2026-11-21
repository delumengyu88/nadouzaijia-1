import csv
import io
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from nadou.admin.audit import AuditWriter
from nadou.admin.errors import WorkbenchError
from nadou.admin.members import EntitlementBook
from nadou.admin.notify import Notifier
from nadou.admin.phone import PhoneMask
from nadou.models import Entitlement, Order, OrderIssuance, User


class OrderLookup:
    @staticmethod
    def by_order_number(session: Session, order_number: str) -> Order | None:
        number = order_number.strip()
        if not number:
            return None
        return session.scalar(select(Order).where(Order.order_number == number))

    @staticmethod
    def by_id(session: Session, order_id: str) -> Order:
        order = session.get(Order, order_id)
        if order is None:
            raise WorkbenchError(404, "order not found")
        return order


class OrderView:
    @staticmethod
    def render(order: Order, phone: str | None, *, reveal_phone: bool) -> dict:
        shown = phone if reveal_phone else PhoneMask.apply(phone)
        return {
            "id": order.id,
            "order_number": order.order_number,
            "user_id": order.user_id,
            "phone": shown,
            "tier": order.tier,
            "duration_days": order.duration_days,
            "amount": order.amount,
            "status": order.status,
            "created_at": order.created_at.isoformat(),
        }


class OrderExport:
    HEADERS = (
        "order_number",
        "status",
        "tier",
        "duration_days",
        "amount",
        "phone",
        "created_at",
    )

    @staticmethod
    def csv(rows: list[dict]) -> str:
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(OrderExport.HEADERS)
        for row in rows:
            writer.writerow(
                [row[name] if row[name] is not None else "" for name in OrderExport.HEADERS]
            )
        return buffer.getvalue()


class OrderGrantWriter:
    """Creates the entitlement captured on the original order. Tier and days are not arguments."""

    def write(
        self, session: Session, user_id: str, tier: str, days: int, now: datetime
    ) -> Entitlement:
        if tier not in {"regular", "vip"}:
            raise ValueError("order tier is not a membership")
        if days < 1:
            raise ValueError("order duration is invalid")
        entitlement = Entitlement(
            user_id=user_id,
            kind=tier,
            program_name=None,
            record_id=None,
            expires_at=now + timedelta(days=days),
            source=EntitlementBook.ORDER,
        )
        session.add(entitlement)
        session.flush()
        return entitlement


class EntitlementIssuer:
    """Issue the order's original tier and duration. A second call does not grant again."""

    MAX_ATTEMPTS = 3

    def __init__(
        self, session: Session, writer: OrderGrantWriter, notifier: Notifier, now: datetime
    ) -> None:
        self.session = session
        self.writer = writer
        self.notifier = notifier
        self.now = now

    def issue(self, order: Order) -> OrderIssuance:
        issuance = self._get_or_create(order)
        if issuance.status in {"issued", "发放异常"}:
            return issuance
        self._attempt(order, issuance, reissue=False)
        return issuance

    def reissue(self, order: Order) -> OrderIssuance:
        issuance = self._get_or_create(order)
        if issuance.status == "issued" and issuance.reissued:
            return issuance
        if order.status != "发放异常" and issuance.status != "发放异常":
            raise WorkbenchError(409, "order is not 发放异常")
        if issuance.status == "issued":
            raise WorkbenchError(409, "order is not 发放异常")
        issuance.attempts = 0
        self._attempt(order, issuance, reissue=True)
        return issuance

    def _attempt(self, order: Order, issuance: OrderIssuance, *, reissue: bool) -> None:
        last_error = None
        for _ in range(EntitlementIssuer.MAX_ATTEMPTS):
            issuance.attempts += 1
            issuance.updated_at = self.now
            try:
                entitlement = self.writer.write(
                    self.session,
                    order.user_id,
                    issuance.tier,
                    issuance.duration_days,
                    self.now,
                )
            except Exception as exc:
                last_error = str(exc)[:500] or exc.__class__.__name__
                issuance.last_error = last_error
                continue
            issuance.status = "issued"
            issuance.entitlement_id = entitlement.id
            issuance.last_error = None
            issuance.reissued = reissue or issuance.reissued
            order.status = "已发放"
            self.session.flush()
            return
        issuance.status = "发放异常"
        issuance.last_error = last_error
        order.status = "发放异常"
        self.session.flush()
        self.notifier.notify("权益发放异常", f"订单 {order.order_number} 连续发放失败")

    def _get_or_create(self, order: Order) -> OrderIssuance:
        existing = self.session.scalar(
            select(OrderIssuance).where(OrderIssuance.order_id == order.id)
        )
        if existing is not None:
            return existing
        row = OrderIssuance(
            order_id=order.id,
            status="pending",
            attempts=0,
            tier=order.tier,
            duration_days=order.duration_days,
            entitlement_id=None,
            last_error=None,
            reissued=False,
            created_at=self.now,
            updated_at=self.now,
        )
        try:
            with self.session.begin_nested():
                self.session.add(row)
                self.session.flush()
        except IntegrityError:
            self.session.expunge(row)
            found = self.session.scalar(
                select(OrderIssuance).where(OrderIssuance.order_id == order.id)
            )
            if found is None:
                raise
            return found
        return row


class OrderDirectory:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_orders(self) -> list[tuple[Order, str | None]]:
        rows = list(
            self.session.scalars(select(Order).order_by(Order.created_at.desc(), Order.id)).all()
        )
        return [(order, self._phone(order.user_id)) for order in rows]

    def one(self, order: Order) -> tuple[Order, str | None]:
        return order, self._phone(order.user_id)

    def _phone(self, user_id: str) -> str | None:
        user = self.session.get(User, user_id)
        if user is None:
            return None
        return user.phone


class OrderAudit:
    """Present so routes record the reissue through the shared writer."""

    @staticmethod
    def reissue(
        audit: AuditWriter, order: Order, before_status: str, issuance: OrderIssuance
    ) -> None:
        audit.write(
            "order",
            order.id,
            "reissue_entitlement",
            {
                "status": before_status,
                "tier": issuance.tier,
                "duration_days": issuance.duration_days,
            },
            {
                "status": order.status,
                "tier": issuance.tier,
                "duration_days": issuance.duration_days,
                "entitlement_id": issuance.entitlement_id,
                "attempts": issuance.attempts,
            },
        )
