from datetime import datetime
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nadou.admin.audit import AuditWriter
from nadou.admin.errors import WorkbenchError
from nadou.admin.notify import Notifier
from nadou.admin.phone import PhoneMask
from nadou.admin.working_days import WorkingDays
from nadou.models import Entitlement, EvidenceNote, EvidenceRequest, User
from nadou.text import RecordAge


def membership_kind(kinds: set[str]) -> str:
    if "vip" in kinds:
        return "vip"
    if "regular" in kinds:
        return "regular"
    return "free"


class EvidenceStatus:
    SUBMITTED = "已提交"
    ACCEPTED = "已受理"
    AWAITING_PAYMENT = "待付款（线下）"
    IN_PROGRESS = "取证中"
    DELIVERED = "已交付"
    COMPLETED = "已完成"
    FAILED = "取证失败"
    CLOSED = "已关闭"
    CANCELLED = "已取消"
    UNPAID = frozenset({ACCEPTED, AWAITING_PAYMENT})
    FLOW = (
        SUBMITTED,
        ACCEPTED,
        AWAITING_PAYMENT,
        IN_PROGRESS,
        DELIVERED,
        COMPLETED,
    )


class DeliveryMethod:
    CHOICES = frozenset({"当面", "快递", "企业网盘", "其他"})
    OTHER = "其他"


class RefundMethod:
    CHOICES = frozenset({"原路退回", "银行转账", "微信转账", "其他"})


class EvidenceLookup:
    @staticmethod
    def by_application_number(session: Session, application_number: str) -> EvidenceRequest | None:
        number = application_number.strip()
        if not number:
            return None
        return session.scalar(
            select(EvidenceRequest).where(EvidenceRequest.application_number == number)
        )


class ApplicationNumbers:
    @staticmethod
    def next_value(session: Session, now: datetime) -> str:
        prefix = "QZ" + now.astimezone(RecordAge.TZ).strftime("%Y%m%d")
        count = session.scalar(
            select(func.count())
            .select_from(EvidenceRequest)
            .where(EvidenceRequest.application_number.startswith(prefix))
        )
        return f"{prefix}{(count or 0) + 1:04d}"


class EvidenceOverdue:
    @staticmethod
    def is_overdue(request: EvidenceRequest, now: datetime) -> bool:
        if request.status != EvidenceStatus.SUBMITTED:
            return False
        deadline = WorkingDays.add(request.created_at, 1)
        return now.astimezone(RecordAge.TZ) >= deadline


class EvidenceOverdueScanner:
    def __init__(self, session: Session, notifier: Notifier, now) -> None:
        self.session = session
        self.notifier = notifier
        self.now = now

    def scan(self) -> int:
        current = self.now() if callable(self.now) else self.now
        rows = list(
            self.session.scalars(
                select(EvidenceRequest).where(
                    EvidenceRequest.status == EvidenceStatus.SUBMITTED,
                    EvidenceRequest.overdue_notified_at.is_(None),
                )
            ).all()
        )
        sent = 0
        for row in rows:
            if not EvidenceOverdue.is_overdue(row, current):
                continue
            self.notifier.notify(
                "取证申请逾期",
                f"申请 {row.application_number} 超过 1 个工作日未受理",
            )
            row.overdue_notified_at = current
            sent += 1
        if sent:
            self.session.flush()
        return sent


class EvidenceView:
    @staticmethod
    def summary(request: EvidenceRequest, *, overdue: bool, vip: bool) -> dict:
        return {
            "id": request.id,
            "application_number": request.application_number,
            "user_id": request.user_id,
            "request_type": request.request_type,
            "status": request.status,
            "created_at": request.created_at.isoformat(),
            "overdue": overdue,
            "vip": vip,
            "quote_amount": request.quote_amount,
            "payment_amount": request.payment_amount,
            "program_name": request.program_name,
            "platform_label": request.platform_label,
            "contact_name": request.contact_name,
            "phone": None,
            "membership": "free",
            "delivered_at": EvidenceView._iso(request.delivered_at),
        }

    @staticmethod
    def detail(
        request: EvidenceRequest,
        notes: list[EvidenceNote],
        *,
        overdue: bool,
        vip: bool,
        phone: str | None,
        reveal_phone: bool,
    ) -> dict:
        body = EvidenceView.summary(request, overdue=overdue, vip=vip)
        shown = phone if reveal_phone else PhoneMask.apply(phone)
        body["phone"] = shown
        body.update(
            {
                "phone": shown,
                "accepted_at": EvidenceView._iso(request.accepted_at),
                "payment_confirmed_at": EvidenceView._iso(request.payment_confirmed_at),
                "delivery_method": request.delivery_method,
                "delivery_note": request.delivery_note,
                "delivered_at": EvidenceView._iso(request.delivered_at),
                "failure_reason": request.failure_reason,
                "refund_method": request.refund_method,
                "refund_amount": request.refund_amount,
                "refund_note": request.refund_note,
                "failed_at": EvidenceView._iso(request.failed_at),
                "quoted_at": EvidenceView._iso(request.quoted_at),
                "completed_at": EvidenceView._iso(request.completed_at),
                "closed_at": EvidenceView._iso(request.closed_at),
                "cancelled_at": EvidenceView._iso(request.cancelled_at),
                "timeline": EvidenceView.timeline(request),
                "record_ref": request.record_ref,
                "organization": request.organization,
                "requirement": request.requirement,
                "notes": [EvidenceView.note(note) for note in notes],
            }
        )
        return body

    @staticmethod
    def note(note: EvidenceNote) -> dict:
        return {
            "id": note.id,
            "author": note.author_username,
            "created_at": note.created_at.isoformat(),
            "text": note.body,
        }

    @staticmethod
    def timeline(request: EvidenceRequest) -> list[dict]:
        stamps = {
            EvidenceStatus.SUBMITTED: request.created_at,
            EvidenceStatus.ACCEPTED: request.accepted_at,
            EvidenceStatus.AWAITING_PAYMENT: request.quoted_at,
            EvidenceStatus.IN_PROGRESS: request.payment_confirmed_at,
            EvidenceStatus.DELIVERED: request.delivered_at,
            EvidenceStatus.COMPLETED: request.completed_at,
        }
        if request.status == EvidenceStatus.CANCELLED:
            return [
                EvidenceView._step(EvidenceStatus.SUBMITTED, request.created_at, "done"),
                EvidenceView._step(EvidenceStatus.CANCELLED, request.cancelled_at, "current"),
            ]
        if request.status in {EvidenceStatus.FAILED, EvidenceStatus.CLOSED}:
            passed = [
                EvidenceView._step(label, stamps[label], "done")
                for label in EvidenceStatus.FLOW
                if label != EvidenceStatus.COMPLETED and stamps[label] is not None
            ]
            passed.append(EvidenceView._step(EvidenceStatus.FAILED, request.failed_at, "done"))
            if request.status == EvidenceStatus.CLOSED:
                passed.append(
                    EvidenceView._step(EvidenceStatus.CLOSED, request.closed_at, "current")
                )
            else:
                passed[-1]["state"] = "current"
            return passed
        index = EvidenceStatus.FLOW.index(request.status)
        rows = []
        for position, label in enumerate(EvidenceStatus.FLOW):
            if position < index:
                state = "done"
            elif position == index:
                state = "current"
            else:
                state = "wait"
            rows.append(EvidenceView._step(label, stamps[label], state))
        return rows

    @staticmethod
    def _step(label: str, when: datetime | None, state: str) -> dict:
        return {"label": label, "at": EvidenceView._iso(when), "state": state}

    @staticmethod
    def _iso(value: datetime | None) -> str | None:
        if value is None:
            return None
        return value.isoformat()


class EvidenceSnapshot:
    @staticmethod
    def of(request: EvidenceRequest) -> dict:
        return {
            "status": request.status,
            "quote_amount": request.quote_amount,
            "payment_amount": request.payment_amount,
            "delivery_method": request.delivery_method,
            "delivery_note": request.delivery_note,
            "failure_reason": request.failure_reason,
            "refund_method": request.refund_method,
            "refund_amount": request.refund_amount,
            "refund_note": request.refund_note,
            "application_number": request.application_number,
        }


class EvidenceWorkflow:
    def __init__(self, session: Session, notifier: Notifier, audit: AuditWriter) -> None:
        self.session = session
        self.notifier = notifier
        self.audit = audit

    def create(
        self,
        user_id: str,
        request_type: str,
        now: datetime,
        application_number: str | None = None,
    ) -> EvidenceRequest:
        user = self.session.get(User, user_id)
        if user is None:
            raise WorkbenchError(404, "user not found")
        kind = request_type.strip()
        if not kind or len(kind) > 32:
            raise WorkbenchError(400, "request type is required")
        number = (application_number or "").strip() or ApplicationNumbers.next_value(
            self.session, now
        )
        if len(number) > 32:
            raise WorkbenchError(400, "application number is too long")
        if EvidenceLookup.by_application_number(self.session, number) is not None:
            raise WorkbenchError(409, "application number already exists")
        row = EvidenceRequest(
            id=str(uuid4()),
            application_number=number,
            user_id=user.id,
            request_type=kind,
            status=EvidenceStatus.SUBMITTED,
            created_at=now,
        )
        self.session.add(row)
        self.session.flush()
        self.audit.write(
            "evidence_request",
            row.id,
            "create_evidence_request",
            None,
            {"application_number": number, "request_type": kind, "user_id": user.id},
        )
        self.notifier.notify("新取证申请", f"申请 {number} 类型 {kind} 已提交")
        return row

    def accept(self, request_id: str, now: datetime) -> EvidenceRequest:
        row = self._require(request_id)
        self._expect(row, EvidenceStatus.SUBMITTED, "only a submitted request can be accepted")
        before = EvidenceSnapshot.of(row)
        row.status = EvidenceStatus.ACCEPTED
        row.accepted_at = now
        self.session.flush()
        self.audit.write(
            "evidence_request", row.id, "accept_evidence", before, EvidenceSnapshot.of(row)
        )
        return row

    def set_quote(self, request_id: str, amount: int, now: datetime) -> EvidenceRequest:
        row = self._require(request_id)
        self._expect(row, EvidenceStatus.ACCEPTED, "quote is only for an accepted request")
        if amount <= 0:
            raise WorkbenchError(400, "amount must be positive")
        before = EvidenceSnapshot.of(row)
        row.quote_amount = amount
        row.quoted_at = now
        row.status = EvidenceStatus.AWAITING_PAYMENT
        self.session.flush()
        self.audit.write("evidence_request", row.id, "set_quote", before, EvidenceSnapshot.of(row))
        return row

    def confirm_payment(self, request_id: str, amount: int, now: datetime) -> EvidenceRequest:
        row = self._require(request_id)
        self._expect(
            row,
            EvidenceStatus.AWAITING_PAYMENT,
            "payment is only for a request awaiting offline payment",
        )
        if amount <= 0:
            raise WorkbenchError(400, "amount must be positive")
        before = EvidenceSnapshot.of(row)
        row.payment_amount = amount
        row.payment_confirmed_at = now
        row.status = EvidenceStatus.IN_PROGRESS
        self.session.flush()
        self.audit.write(
            "evidence_request", row.id, "confirm_payment", before, EvidenceSnapshot.of(row)
        )
        return row

    def deliver(
        self, request_id: str, method: str, note: str | None, now: datetime
    ) -> EvidenceRequest:
        row = self._require(request_id)
        self._expect(row, EvidenceStatus.IN_PROGRESS, "delivery is only for a request in progress")
        if method not in DeliveryMethod.CHOICES:
            raise WorkbenchError(400, "delivery method is invalid")
        text = (note or "").strip()
        if method == DeliveryMethod.OTHER and not text:
            raise WorkbenchError(400, "delivery note is required when method is 其他")
        if len(text) > 500:
            raise WorkbenchError(400, "delivery note is too long")
        before = EvidenceSnapshot.of(row)
        row.delivery_method = method
        row.delivery_note = text or None
        row.delivered_at = now
        row.status = EvidenceStatus.DELIVERED
        self.session.flush()
        self.audit.write(
            "evidence_request", row.id, "record_delivery", before, EvidenceSnapshot.of(row)
        )
        return row

    def fail(
        self,
        request_id: str,
        reason: str,
        now: datetime,
        refund_method: str | None = None,
        refund_amount: int | None = None,
        refund_note: str | None = None,
    ) -> EvidenceRequest:
        row = self._require(request_id)
        text = reason.strip()
        if not text:
            raise WorkbenchError(400, "failure reason is required")
        if len(text) > 500:
            raise WorkbenchError(400, "failure reason is too long")
        method = (refund_method or "").strip()
        if row.status in EvidenceStatus.UNPAID:
            if method or refund_amount is not None:
                raise WorkbenchError(400, "unpaid request does not accept refund fields")
            stored_method = None
            stored_amount = None
            stored_note = None
        elif row.status == EvidenceStatus.IN_PROGRESS:
            note = (refund_note or "").strip()
            if method not in RefundMethod.CHOICES:
                raise WorkbenchError(400, "refund method is required")
            amount = row.payment_amount if refund_amount is None else refund_amount
            if amount is None or amount < 0:
                raise WorkbenchError(400, "refund amount is required")
            if not note:
                raise WorkbenchError(400, "refund note is required")
            if len(note) > 200:
                raise WorkbenchError(400, "refund note is too long")
            stored_method = method
            stored_amount = amount
            stored_note = note
        else:
            raise WorkbenchError(409, "request cannot be marked failed from its current status")
        before = EvidenceSnapshot.of(row)
        row.status = EvidenceStatus.FAILED
        row.failure_reason = text
        row.refund_method = stored_method
        row.refund_amount = stored_amount
        row.refund_note = stored_note
        row.failed_at = now
        self.session.flush()
        self.audit.write(
            "evidence_request", row.id, "fail_evidence", before, EvidenceSnapshot.of(row)
        )
        return row

    def complete(self, request_id: str, now: datetime) -> EvidenceRequest:
        row = self._require(request_id)
        self._expect(row, EvidenceStatus.DELIVERED, "only a delivered request can be completed")
        before = EvidenceSnapshot.of(row)
        row.status = EvidenceStatus.COMPLETED
        row.completed_at = now
        self.session.flush()
        self.audit.write(
            "evidence_request", row.id, "complete_evidence", before, EvidenceSnapshot.of(row)
        )
        return row

    def close(self, request_id: str, now: datetime) -> EvidenceRequest:
        row = self._require(request_id)
        self._expect(row, EvidenceStatus.FAILED, "only a failed request can be closed")
        before = EvidenceSnapshot.of(row)
        row.status = EvidenceStatus.CLOSED
        row.closed_at = now
        self.session.flush()
        self.audit.write(
            "evidence_request", row.id, "close_evidence", before, EvidenceSnapshot.of(row)
        )
        return row

    def cancel(self, request_id: str, now: datetime) -> EvidenceRequest:
        row = self._require(request_id)
        self._expect(row, EvidenceStatus.SUBMITTED, "only a submitted request can be cancelled")
        before = EvidenceSnapshot.of(row)
        row.status = EvidenceStatus.CANCELLED
        row.cancelled_at = now
        self.session.flush()
        self.audit.write(
            "evidence_request", row.id, "cancel_evidence", before, EvidenceSnapshot.of(row)
        )
        return row

    def add_note(self, request_id: str, author, text: str, now: datetime) -> EvidenceNote:
        row = self._require(request_id)
        body = text.strip()
        if not body:
            raise WorkbenchError(400, "note text is required")
        if len(body) > 2000:
            raise WorkbenchError(400, "note text is too long")
        note = EvidenceNote(
            request_id=row.id,
            author_id=author.id,
            author_username=author.username,
            created_at=now,
            body=body,
        )
        self.session.add(note)
        self.session.flush()
        self.audit.write(
            "evidence_note",
            str(note.id),
            "add_evidence_note",
            None,
            {
                "request_id": row.id,
                "application_number": row.application_number,
                "author": author.username,
                "text": body,
            },
        )
        return note

    @staticmethod
    def notes_for(session: Session, request_id: str) -> list[EvidenceNote]:
        return list(
            session.scalars(
                select(EvidenceNote)
                .where(EvidenceNote.request_id == request_id)
                .order_by(EvidenceNote.created_at, EvidenceNote.id)
            ).all()
        )

    def _require(self, request_id: str) -> EvidenceRequest:
        row = self.session.get(EvidenceRequest, request_id)
        if row is None:
            raise WorkbenchError(404, "evidence request not found")
        return row

    @staticmethod
    def _expect(row: EvidenceRequest, status: str, message: str) -> None:
        if row.status != status:
            raise WorkbenchError(409, message)


class EvidenceList:
    def __init__(self, session: Session, now: datetime) -> None:
        self.session = session
        self.now = now

    def query(
        self,
        *,
        status: str | None,
        request_type: str | None,
        created_from: datetime | None,
        created_to: datetime | None,
        vip_first: bool,
        reveal_phone: bool = True,
        created_before: datetime | None = None,
    ) -> list[dict]:
        stmt = select(EvidenceRequest)
        if status:
            stmt = stmt.where(EvidenceRequest.status == status)
        if request_type:
            stmt = stmt.where(EvidenceRequest.request_type == request_type)
        if created_from is not None:
            stmt = stmt.where(EvidenceRequest.created_at >= created_from)
        if created_to is not None:
            stmt = stmt.where(EvidenceRequest.created_at <= created_to)
        if created_before is not None:
            stmt = stmt.where(EvidenceRequest.created_at < created_before)
        rows = list(self.session.scalars(stmt).all())
        vip_ids = self._vip_ids({row.user_id for row in rows})
        rows.sort(
            key=lambda row: (
                0 if EvidenceOverdue.is_overdue(row, self.now) else 1,
                0 if vip_first and row.user_id in vip_ids else 1,
                -row.created_at.timestamp(),
                row.id,
            )
        )
        phones = self._phones({row.user_id for row in rows})
        regular_ids = self._regular_ids({row.user_id for row in rows})
        listed = []
        for row in rows:
            kinds = set()
            if row.user_id in vip_ids:
                kinds.add("vip")
            if row.user_id in regular_ids:
                kinds.add("regular")
            membership = membership_kind(kinds)
            body = EvidenceView.summary(
                row,
                overdue=EvidenceOverdue.is_overdue(row, self.now),
                vip=row.user_id in vip_ids,
            )
            body["membership"] = membership
            phone = phones.get(row.user_id)
            body["phone"] = phone if reveal_phone else PhoneMask.apply(phone)
            listed.append(body)
        return listed

    def _vip_ids(self, user_ids: set[str]) -> set[str]:
        if not user_ids:
            return set()
        rows = self.session.scalars(
            select(Entitlement.user_id).where(
                Entitlement.user_id.in_(user_ids),
                Entitlement.kind == "vip",
                Entitlement.expires_at > self.now,
            )
        ).all()
        return set(rows)

    def _regular_ids(self, user_ids: set[str]) -> set[str]:
        if not user_ids:
            return set()
        rows = self.session.scalars(
            select(Entitlement.user_id).where(
                Entitlement.user_id.in_(user_ids),
                Entitlement.kind == "regular",
                Entitlement.expires_at > self.now,
            )
        ).all()
        return set(rows)

    def _phones(self, user_ids: set[str]) -> dict[str, str | None]:
        if not user_ids:
            return {}
        users = self.session.scalars(select(User).where(User.id.in_(user_ids))).all()
        return {user.id: user.phone for user in users}
