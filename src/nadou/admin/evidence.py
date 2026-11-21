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


class EvidenceStatus:
    PENDING = "待受理"
    ACCEPTED = "已受理"
    IN_PROGRESS = "取证中"
    DELIVERED = "已交付"
    FAILED = "已失败"


class DeliveryMethod:
    CHOICES = frozenset({"当面", "快递", "企业网盘", "其他"})
    OTHER = "其他"


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
        if request.status != EvidenceStatus.PENDING:
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
                    EvidenceRequest.status == EvidenceStatus.PENDING,
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
                "failed_at": EvidenceView._iso(request.failed_at),
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
            status=EvidenceStatus.PENDING,
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
        self._expect(row, EvidenceStatus.PENDING, "only a pending request can be accepted")
        before = EvidenceSnapshot.of(row)
        row.status = EvidenceStatus.ACCEPTED
        row.accepted_at = now
        self.session.flush()
        self.audit.write(
            "evidence_request", row.id, "accept_evidence", before, EvidenceSnapshot.of(row)
        )
        return row

    def set_quote(self, request_id: str, amount: int) -> EvidenceRequest:
        row = self._require(request_id)
        self._expect(row, EvidenceStatus.ACCEPTED, "quote is only for an accepted unpaid request")
        if amount <= 0:
            raise WorkbenchError(400, "amount must be positive")
        before = EvidenceSnapshot.of(row)
        row.quote_amount = amount
        self.session.flush()
        self.audit.write("evidence_request", row.id, "set_quote", before, EvidenceSnapshot.of(row))
        return row

    def confirm_payment(self, request_id: str, amount: int, now: datetime) -> EvidenceRequest:
        row = self._require(request_id)
        self._expect(row, EvidenceStatus.ACCEPTED, "payment is only for an accepted unpaid request")
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
    ) -> EvidenceRequest:
        row = self._require(request_id)
        text = reason.strip()
        if not text:
            raise WorkbenchError(400, "failure reason is required")
        if len(text) > 500:
            raise WorkbenchError(400, "failure reason is too long")
        method = (refund_method or "").strip()
        if row.status == EvidenceStatus.ACCEPTED:
            if method or refund_amount is not None:
                raise WorkbenchError(400, "unpaid request does not accept refund fields")
            stored_method = None
            stored_amount = None
        elif row.status == EvidenceStatus.IN_PROGRESS:
            if not method:
                raise WorkbenchError(400, "refund method is required")
            if len(method) > 32:
                raise WorkbenchError(400, "refund method is too long")
            amount = row.payment_amount if refund_amount is None else refund_amount
            if amount is None or amount < 0:
                raise WorkbenchError(400, "refund amount is required")
            stored_method = method
            stored_amount = amount
        else:
            raise WorkbenchError(409, "request cannot be marked failed from its current status")
        before = EvidenceSnapshot.of(row)
        row.status = EvidenceStatus.FAILED
        row.failure_reason = text
        row.refund_method = stored_method
        row.refund_amount = stored_amount
        row.failed_at = now
        self.session.flush()
        self.audit.write(
            "evidence_request", row.id, "fail_evidence", before, EvidenceSnapshot.of(row)
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
        listed = []
        for row in rows:
            body = EvidenceView.summary(
                row,
                overdue=EvidenceOverdue.is_overdue(row, self.now),
                vip=row.user_id in vip_ids,
            )
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

    def _phones(self, user_ids: set[str]) -> dict[str, str | None]:
        if not user_ids:
            return {}
        users = self.session.scalars(select(User).where(User.id.in_(user_ids))).all()
        return {user.id: user.phone for user in users}
