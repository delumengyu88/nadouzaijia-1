import csv
import io
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from nadou.admin.audit import AuditWriter
from nadou.admin.errors import WorkbenchError
from nadou.admin.members import EntitlementBook
from nadou.admin.notify import Notifier
from nadou.admin.phone import PhoneMask
from nadou.bands import BAND_KINDS
from nadou.models import Entitlement, Order, OrderIssuance, User


class OrderLookup:
    @staticmethod
    def lock_membership(session: Session, order: Order) -> None:
        """Serialize membership mutations for one owner, including the first purchase."""
        if session.get_bind().dialect.name == "sqlite":
            session.execute(update(User).where(User.id == order.user_id).values(id=User.id))
        else:
            from nadou.payments.notify_io import bounded_row_lock

            bounded_row_lock(
                session, select(User.id).where(User.id == order.user_id).with_for_update()
            )
        session.refresh(order)

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
    def render(
        order: Order,
        phone: str | None,
        *,
        reveal_phone: bool,
        issuance: OrderIssuance | None = None,
    ) -> dict:
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
            "client": order.client,
            "paid_at": order.paid_at.isoformat() if order.paid_at else None,
            "refunded_at": order.refunded_at.isoformat() if order.refunded_at else None,
            "refund_reason": order.refund_reason,
            "product": order.product,
            "amount_fen": order.amount_fen,
            "wechat_transaction_id": order.wechat_transaction_id,
            "program_name": order.program_name,
            "attempts": None if issuance is None else issuance.attempts,
            "last_error": None if issuance is None else issuance.last_error,
            "issuance_status": None if issuance is None else issuance.status,
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
    """Creates the entitlement captured on the original order. Tier and days are not arguments.

    The new row ends at max(this tier's live expiry, now) plus the order's days. Another
    tier is not a base and is not rewritten. A repeated issuance does not call ``write``.
    """

    @staticmethod
    def renewal_expiry(now: datetime, days: int, current_expiry: datetime | None) -> datetime:
        """Add ``days`` to the later of ``current_expiry`` and ``now``."""
        start = now if current_expiry is None else max(current_expiry, now)
        return start + timedelta(days=days)

    @staticmethod
    def live_tier_expiry(
        session: Session, user_id: str, tier: str, now: datetime
    ) -> datetime | None:
        """Latest unexpired expiry of this tier, across every source.

        This is the same instant ``vip_expires_at`` / ``regular_expires_at`` already show.
        An expiry equal to ``now`` has already lapsed and is not a base.
        """
        rows = session.scalars(
            select(Entitlement.expires_at).where(
                Entitlement.user_id == user_id,
                Entitlement.kind == tier,
                Entitlement.expires_at > now,
            )
        ).all()
        if not rows:
            return None
        return max(rows)

    def write(
        self, session: Session, user_id: str, tier: str, days: int, now: datetime
    ) -> Entitlement:
        if tier not in {"regular", "vip"}:
            raise ValueError("order tier is not a membership")
        if days < 1:
            raise ValueError("order duration is invalid")
        expires_at = OrderGrantWriter.renewal_expiry(
            now,
            days,
            OrderGrantWriter.live_tier_expiry(session, user_id, tier, now),
        )
        entitlement = Entitlement(
            user_id=user_id,
            kind=tier,
            program_name=None,
            record_id=None,
            expires_at=expires_at,
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
        OrderLookup.lock_membership(self.session, order)
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
                if issuance.tier in BAND_KINDS:
                    from nadou.payments.unlock import write_unlock

                    entitlement = write_unlock(self.session, order, None, self.now)
                else:
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
            found = self.session.scalar(
                select(OrderIssuance).where(OrderIssuance.order_id == order.id)
            )
            if found is None:
                raise
            return found
        return row


class OrderRefund:
    """A refund expires the entitlement this order issued. Tier and days are not editable."""

    REFUNDABLE = frozenset({"已发放"})

    def __init__(self, session: Session, audit: AuditWriter, now: datetime) -> None:
        self.session = session
        self.audit = audit
        self.now = now

    def refund(self, order: Order, reason: str) -> Order:
        text = reason.strip()
        OrderLookup.lock_membership(self.session, order)
        if order.status not in OrderRefund.REFUNDABLE:
            raise WorkbenchError(409, "order cannot be refunded")
        if not text:
            raise WorkbenchError(400, "refund reason is required")
        if len(text) > 500:
            raise WorkbenchError(400, "refund reason is too long")
        issuance = self.session.scalar(
            select(OrderIssuance).where(OrderIssuance.order_id == order.id)
        )
        adjustments = []
        before = {"status": order.status, "entitlement_id": None}
        if issuance is not None and issuance.entitlement_id is not None:
            entitlement = self.session.get(Entitlement, issuance.entitlement_id)
            before["entitlement_id"] = issuance.entitlement_id
            if (
                entitlement is not None
                and entitlement.user_id == order.user_id
                and entitlement.kind == issuance.tier == order.tier
                and entitlement.source == EntitlementBook.ORDER
                and issuance.status == "issued"
                and issuance.duration_days > 0
                and entitlement.expires_at > self.now
            ):
                if entitlement.kind in BAND_KINDS:
                    # A band unlock is one program's permanent row; nothing renews on top of it.
                    adjustments = [
                        {
                            "entitlement_id": entitlement.id,
                            "before": entitlement.expires_at,
                            "after": self.now,
                        }
                    ]
                    entitlement.expires_at = self.now
                else:
                    adjustments = self._revoke_remaining(entitlement, issuance)
        order.status = "已退款"
        order.refunded_at = self.now
        order.refund_reason = text
        self.session.flush()
        self.audit.write(
            "order",
            order.id,
            "refund_order",
            before,
            {
                "status": order.status,
                "refund_reason": text,
                "entitlement_id": before["entitlement_id"],
                "expiry_adjustments": adjustments,
            },
        )
        return order

    def _revoke_remaining(self, target: Entitlement, issuance: OrderIssuance) -> list[dict]:
        # Later renewals contain their predecessor's expiry plus their own purchased days.
        # Follow only that exact boundary and keep alternate grants as a floor.
        later_issues = {
            row.entitlement_id: row
            for row in self.session.scalars(
                select(OrderIssuance)
                .join(Order, Order.id == OrderIssuance.order_id)
                .where(
                    Order.user_id == target.user_id,
                    Order.tier == target.kind,
                    Order.status == "已发放",
                    OrderIssuance.status == "issued",
                    OrderIssuance.tier == target.kind,
                    OrderIssuance.duration_days > 0,
                    OrderIssuance.entitlement_id > target.id,
                )
            )
        }
        grants = self.session.scalars(
            select(Entitlement)
            .where(
                Entitlement.user_id == target.user_id,
                Entitlement.kind == target.kind,
                Entitlement.expires_at > self.now,
            )
            .order_by(Entitlement.id)
            .with_for_update()
        ).all()
        prior_expiry = self.now
        changed_boundaries = set()
        adjustments = []
        for grant in grants:
            old_end = grant.expires_at
            if grant.id == target.id:
                changed_boundaries.add(old_end)
                prior_expiry = max(prior_expiry, old_end - timedelta(days=issuance.duration_days))
                grant.expires_at = self.now
            elif grant.source == EntitlementBook.ORDER and (later := later_issues.get(grant.id)):
                duration = timedelta(days=later.duration_days)
                old_start = old_end - duration
                if old_start in changed_boundaries and old_start > max(
                    prior_expiry, later.updated_at
                ):
                    grant.expires_at = max(prior_expiry, later.updated_at) + duration
                    changed_boundaries.add(old_end)
            if old_end != grant.expires_at:
                adjustments.append(
                    {"entitlement_id": grant.id, "before": old_end, "after": grant.expires_at}
                )
            prior_expiry = max(prior_expiry, grant.expires_at)
        return adjustments


class OrderDirectory:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_orders(self) -> list[tuple[Order, str | None, OrderIssuance | None]]:
        rows = list(
            self.session.scalars(select(Order).order_by(Order.created_at.desc(), Order.id)).all()
        )
        return [(order, self._phone(order.user_id), self.issuance(order)) for order in rows]

    def one(self, order: Order) -> tuple[Order, str | None, OrderIssuance | None]:
        return order, self._phone(order.user_id), self.issuance(order)

    def issuance(self, order: Order) -> OrderIssuance | None:
        return self.session.scalar(select(OrderIssuance).where(OrderIssuance.order_id == order.id))

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
