"""Member evidence requests. The workbench keeps its own routes and Chinese statuses."""

import re
import unicodedata
from datetime import datetime
from uuid import uuid4

from fastapi import BackgroundTasks, Depends, FastAPI, Header, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from nadou.admin.audit import AuditWriter
from nadou.admin.evidence import ApplicationNumbers, EvidenceSnapshot, EvidenceStatus
from nadou.admin.notify import Notifier
from nadou.auth import UserSessions
from nadou.models import EvidenceRequest, User


class EvidenceUserError(Exception):
    """A failure the member can read. The body is always `{code, message}`."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        extra: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.extra = extra or {}


class EvidenceUserStatus:
    CODES = {
        EvidenceStatus.SUBMITTED: "submitted",
        EvidenceStatus.ACCEPTED: "accepted",
        EvidenceStatus.AWAITING_PAYMENT: "pay",
        EvidenceStatus.IN_PROGRESS: "working",
        EvidenceStatus.DELIVERED: "delivered",
        EvidenceStatus.COMPLETED: "done",
        EvidenceStatus.FAILED: "failed",
        EvidenceStatus.CLOSED: "closed",
        EvidenceStatus.CANCELLED: "cancelled",
    }

    @staticmethod
    def code(stored: str) -> str:
        return EvidenceUserStatus.CODES[stored]


class EvidenceUserKind:
    STORED = {"normal": "常规", "recent": "近期"}
    PUBLIC = {"常规": "normal", "近期": "recent"}
    LABELS = {"常规": "常规取证", "近期": "近期取证"}

    @staticmethod
    def require(value: object) -> str:
        if isinstance(value, str) and value in EvidenceUserKind.STORED:
            return EvidenceUserKind.STORED[value]
        raise EvidenceUserError(422, "invalid_kind", "取证类型不正确")

    @staticmethod
    def public(stored: str) -> str:
        return EvidenceUserKind.PUBLIC.get(stored, stored)

    @staticmethod
    def label(stored: str) -> str:
        return EvidenceUserKind.LABELS.get(stored, stored)


class EvidenceUserName:
    """Compare program names after trim and NFKC, so full-width letters match."""

    @staticmethod
    def normalize(value: object) -> str:
        if not isinstance(value, str):
            raise EvidenceUserError(422, "name_required", "请填写节目名")
        name = unicodedata.normalize("NFKC", value.strip()).strip()
        if not name:
            raise EvidenceUserError(422, "name_required", "请填写节目名")
        if len(name) > 200:
            raise EvidenceUserError(422, "name_too_long", "节目名不能超过 200 字")
        return name


class EvidenceUserPhone:
    PATTERN = re.compile(r"^1\d{10}$")

    @staticmethod
    def mask(phone: str) -> str:
        if len(phone) < 7:
            return "****"
        return f"{phone[:3]}****{phone[-4:]}"

    @staticmethod
    def require(value: object) -> str:
        if value is None:
            raise EvidenceUserError(422, "phone_required", "请填写联系手机号")
        if not isinstance(value, str):
            raise EvidenceUserError(422, "invalid_phone", "请填写正确的手机号")
        candidate = value.strip()
        if not candidate:
            raise EvidenceUserError(422, "phone_required", "请填写联系手机号")
        if EvidenceUserPhone.PATTERN.fullmatch(candidate) is None:
            raise EvidenceUserError(422, "invalid_phone", "请填写正确的手机号")
        return candidate


class EvidenceUserNote:
    @staticmethod
    def clean(value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise EvidenceUserError(422, "invalid_note", "备注格式不正确")
        text_value = value.strip()
        if len(text_value) > 500:
            raise EvidenceUserError(422, "note_too_long", "备注不能超过 500 字")
        return text_value or None


class EvidenceUserPlatform:
    @staticmethod
    def clean(kind: object, value: object) -> str | None:
        if kind == "recent":
            return None
        if value is None:
            return None
        if not isinstance(value, str):
            raise EvidenceUserError(422, "invalid_platform", "平台格式不正确")
        label = value.strip()
        if not label:
            return None
        if len(label) > 200:
            raise EvidenceUserError(422, "platform_too_long", "平台名称不能超过 200 字")
        return label


class EvidenceUserBody:
    @staticmethod
    async def read(request: Request) -> dict:
        try:
            payload = await request.json()
        except Exception:
            raise EvidenceUserError(422, "invalid_body", "请求格式不正确") from None
        if not isinstance(payload, dict):
            raise EvidenceUserError(422, "invalid_body", "请求格式不正确")
        return payload


class EvidenceUserLock:
    """Serialize one member's check and insert.

    Postgres locks that user row for the rest of the transaction.
    SQLite has no row lock. The login lookup is a deferred read; leaving it
    open and then writing deadlocks under two connections. Commit that read,
    then update the user row and hold the write lock until the request
    commits. The second submit waits (`busy_timeout`) and then sees the row.
    """

    @staticmethod
    def hold(session: Session, user_id: str) -> None:
        if session.get_bind().dialect.name != "sqlite":
            session.execute(select(User.id).where(User.id == user_id).with_for_update())
            return
        session.commit()
        session.execute(text("PRAGMA busy_timeout = 8000"))
        session.execute(
            text("UPDATE users SET id = id WHERE id = :user_id"),
            {"user_id": user_id},
        )


class EvidenceUserView:
    @staticmethod
    def summary(row: EvidenceRequest) -> dict:
        return {
            "id": row.id,
            "no": row.application_number,
            "name": row.program_name,
            "platform": row.platform_label,
            "kind": EvidenceUserKind.public(row.request_type),
            "status": EvidenceUserStatus.code(row.status),
            "status_text": row.status,
            "created_at": row.created_at.isoformat(),
        }

    @staticmethod
    def detail(row: EvidenceRequest) -> dict:
        body = EvidenceUserView.summary(row)
        phone = row.contact_phone
        body.update(
            {
                "note": row.requirement,
                "phone": None if not phone else EvidenceUserPhone.mask(phone),
                "quote_amount": row.quote_amount,
                "failure_reason": row.failure_reason,
                "refund_method": row.refund_method,
                "refund_amount": row.refund_amount,
                "refund_note": row.refund_note,
                "accepted_at": EvidenceUserView.iso(row.accepted_at),
                "quoted_at": EvidenceUserView.iso(row.quoted_at),
                "payment_confirmed_at": EvidenceUserView.iso(row.payment_confirmed_at),
                "delivered_at": EvidenceUserView.iso(row.delivered_at),
                "completed_at": EvidenceUserView.iso(row.completed_at),
                "failed_at": EvidenceUserView.iso(row.failed_at),
                "cancelled_at": EvidenceUserView.iso(row.cancelled_at),
                "closed_at": EvidenceUserView.iso(row.closed_at),
            }
        )
        return body

    @staticmethod
    def iso(value: datetime | None) -> str | None:
        if value is None:
            return None
        return value.isoformat()


class EvidenceUserNotice:
    @staticmethod
    def send(notifier: Notifier, name: str, number: str, stored_kind: str, phone: str) -> None:
        masked = EvidenceUserPhone.mask(phone)
        title = "新的委托取证"
        body = "\n".join(
            (
                f"节目名：{name}",
                f"工单号：{number}",
                f"取证类型：{EvidenceUserKind.label(stored_kind)}",
                f"联系电话：{masked}",
            )
        )
        if phone:
            title = title.replace(phone, masked)
            body = body.replace(phone, masked)
        try:
            notifier.notify(title, body)
        except Exception:
            return


class EvidenceUserBook:
    OPEN = (
        EvidenceStatus.SUBMITTED,
        EvidenceStatus.ACCEPTED,
        EvidenceStatus.AWAITING_PAYMENT,
        EvidenceStatus.IN_PROGRESS,
    )

    def __init__(self, session: Session, now: datetime) -> None:
        self.session = session
        self.now = now
        # Members are not staff. The audit row keeps the user id in the payload.
        self.audit = AuditWriter(session, now, None, None)

    def submit(self, user: User, fields: dict) -> EvidenceRequest:
        name = EvidenceUserName.normalize(fields.get("name"))
        kind = fields.get("kind")
        stored_kind = EvidenceUserKind.require(kind)
        platform = EvidenceUserPlatform.clean(kind, fields.get("platform"))
        note = EvidenceUserNote.clean(fields.get("note"))
        phone = EvidenceUserPhone.require(fields.get("phone"))
        EvidenceUserLock.hold(self.session, user.id)
        existing = EvidenceUserBook.find_open(self.session, user.id, name)
        if existing is not None:
            raise EvidenceUserBook.duplicate(existing)
        return self._stage(user.id, name, stored_kind, platform, note, phone)

    def list_for(self, user: User) -> list[dict]:
        rows = self.session.scalars(
            select(EvidenceRequest)
            .where(EvidenceRequest.user_id == user.id)
            .order_by(EvidenceRequest.created_at.desc(), EvidenceRequest.id.desc())
        ).all()
        return [EvidenceUserView.summary(row) for row in rows]

    def get(self, user: User, request_id: str) -> dict:
        return EvidenceUserView.detail(EvidenceUserBook.owned(self.session, user.id, request_id))

    def withdraw(self, user: User, request_id: str) -> dict:
        row = self._locked(user.id, request_id)
        if row.status != EvidenceStatus.SUBMITTED:
            raise EvidenceUserError(409, "invalid_state", "当前状态不能撤回")
        before = EvidenceSnapshot.of(row)
        row.status = EvidenceStatus.CANCELLED
        row.cancelled_at = self.now
        self.session.flush()
        self._audit(row, user.id, "withdraw_evidence", before)
        return EvidenceUserView.detail(row)

    def confirm(self, user: User, request_id: str) -> dict:
        row = self._locked(user.id, request_id)
        if row.status != EvidenceStatus.DELIVERED:
            raise EvidenceUserError(409, "invalid_state", "当前状态不能确认")
        before = EvidenceSnapshot.of(row)
        row.status = EvidenceStatus.COMPLETED
        row.completed_at = self.now
        self.session.flush()
        self._audit(row, user.id, "confirm_evidence", before)
        return EvidenceUserView.detail(row)

    def _locked(self, user_id: str, request_id: str) -> EvidenceRequest:
        EvidenceUserLock.hold(self.session, user_id)
        return EvidenceUserBook.owned(self.session, user_id, request_id)

    def _audit(self, row: EvidenceRequest, user_id: str, action: str, before: dict) -> None:
        after = EvidenceSnapshot.of(row)
        after["user_id"] = user_id
        self.audit.write("evidence_request", row.id, action, before, after)

    def _stage(
        self,
        user_id: str,
        name: str,
        stored_kind: str,
        platform: str | None,
        note: str | None,
        phone: str,
    ) -> EvidenceRequest:
        # record_ref stays empty. Nothing on this path reads index rows or
        # copies a generated date onto the request.
        for _attempt in range(3):
            number = ApplicationNumbers.next_value(self.session, self.now)
            row = EvidenceRequest(
                id=str(uuid4()),
                application_number=number,
                user_id=user_id,
                request_type=stored_kind,
                status=EvidenceStatus.SUBMITTED,
                created_at=self.now,
                program_name=name,
                platform_label=platform,
                requirement=note,
                contact_phone=phone,
                record_ref=None,
            )
            try:
                with self.session.begin_nested():
                    self.session.add(row)
                    self.session.flush()
            except IntegrityError:
                # The savepoint rollback already drops the pending row.
                if row in self.session:
                    self.session.expunge(row)
                continue
            return row
        raise EvidenceUserError(503, "no_unavailable", "提交失败，请稍后再试")

    @staticmethod
    def find_open(session: Session, user_id: str, name: str) -> EvidenceRequest | None:
        return session.scalar(
            select(EvidenceRequest)
            .where(
                EvidenceRequest.user_id == user_id,
                EvidenceRequest.program_name == name,
                EvidenceRequest.status.in_(EvidenceUserBook.OPEN),
            )
            .order_by(EvidenceRequest.created_at.desc(), EvidenceRequest.id.desc())
            .limit(1)
        )

    @staticmethod
    def owned(session: Session, user_id: str, request_id: str) -> EvidenceRequest:
        row = session.get(EvidenceRequest, request_id)
        if row is None or row.user_id != user_id:
            raise EvidenceUserError(404, "not_found", "申请不存在")
        return row

    @staticmethod
    def duplicate(existing: EvidenceRequest) -> EvidenceUserError:
        return EvidenceUserError(
            409,
            "duplicate",
            "这个节目已经提交过取证申请",
            {"id": existing.id, "no": existing.application_number},
        )


class UserEvidence:
    @staticmethod
    def register(app: FastAPI, get_session, clock, notifier: Notifier) -> None:
        def current_user(
            authorization: str | None = Header(default=None),
            session: Session = Depends(get_session, scope="function"),
        ) -> User:
            return UserSessions.resolve(session, authorization, clock())

        @app.exception_handler(EvidenceUserError)
        def user_evidence_error(_request: Request, exc: EvidenceUserError):
            content = {"code": exc.code, **exc.extra, "message": exc.message}
            return JSONResponse(status_code=exc.status_code, content=content)

        @app.post("/api/v1/evidence-requests", status_code=201)
        async def submit_user_evidence(
            request: Request,
            background: BackgroundTasks,
            user: User = Depends(current_user),
            session: Session = Depends(get_session, scope="function"),
        ):
            fields = await EvidenceUserBody.read(request)
            row = EvidenceUserBook(session, clock()).submit(user, fields)
            # Commit before the notice. DingTalk must not keep the user-row lock,
            # and a commit failure must not announce a ticket that was not stored.
            notice = (
                row.program_name or "",
                row.application_number,
                row.request_type,
                row.contact_phone or "",
            )
            session.commit()
            background.add_task(EvidenceUserNotice.send, notifier, *notice)
            return {"id": row.id, "no": row.application_number}

        @app.get("/api/v1/evidence-requests")
        def list_user_evidence(
            user: User = Depends(current_user),
            session: Session = Depends(get_session, scope="function"),
        ):
            return EvidenceUserBook(session, clock()).list_for(user)

        @app.get("/api/v1/evidence-requests/{request_id}")
        def get_user_evidence(
            request_id: str,
            user: User = Depends(current_user),
            session: Session = Depends(get_session, scope="function"),
        ):
            return EvidenceUserBook(session, clock()).get(user, request_id)

        @app.post("/api/v1/evidence-requests/{request_id}/withdraw")
        def withdraw_user_evidence(
            request_id: str,
            user: User = Depends(current_user),
            session: Session = Depends(get_session, scope="function"),
        ):
            return EvidenceUserBook(session, clock()).withdraw(user, request_id)

        @app.post("/api/v1/evidence-requests/{request_id}/confirm")
        def confirm_user_evidence(
            request_id: str,
            user: User = Depends(current_user),
            session: Session = Depends(get_session, scope="function"),
        ):
            return EvidenceUserBook(session, clock()).confirm(user, request_id)
