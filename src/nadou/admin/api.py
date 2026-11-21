import csv
import io
from datetime import datetime

from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from nadou.admin.audit import AuditQuery, AuditWriter
from nadou.admin.auth import (
    ClientAddress,
    RequestToken,
    StaffAuthenticator,
    StaffDirectory,
    StaffSessions,
)
from nadou.admin.content import (
    AnnouncementBook,
    AnnouncementView,
    ExampleBook,
    ExampleView,
    ProgramCatalog,
    SplashBook,
    SplashView,
)
from nadou.admin.errors import WorkbenchError
from nadou.admin.evidence import (
    EvidenceList,
    EvidenceLookup,
    EvidenceOverdue,
    EvidenceOverdueScanner,
    EvidenceView,
    EvidenceWorkflow,
    membership_kind,
)
from nadou.admin.indexing import CsvTaskExportSource, IndexJob, IndexRunBusy, IndexStatus
from nadou.admin.invites import InviteBook, InviteStats, InviteView
from nadou.admin.members import MemberAdmin, MemberDirectory, UserProfile, UserView
from nadou.admin.orders import (
    EntitlementIssuer,
    OrderAudit,
    OrderDirectory,
    OrderExport,
    OrderGrantWriter,
    OrderLookup,
    OrderRefund,
    OrderView,
)
from nadou.admin.schemas import (
    AmountIn,
    AnnouncementIn,
    BanIn,
    DeliveryIn,
    EvidenceCreateIn,
    ExampleIn,
    ExampleMoveIn,
    FailIn,
    ImportIn,
    InviteBatchIn,
    LoginIn,
    MembershipIn,
    NoteIn,
    OnlineIn,
    RefundIn,
    ReissueIn,
    SplashIn,
    StaffCreateIn,
)
from nadou.admin.shanghai import ShanghaiDay
from nadou.config import get_settings
from nadou.models import Entitlement, EvidenceRequest, StaffAccount, User


class Endpoint:
    @staticmethod
    def invoke(action):
        try:
            return action()
        except WorkbenchError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None


class AwareInstant:
    @staticmethod
    def parse(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise HTTPException(status_code=400, detail="datetime must include a timezone offset")
        return value


class AdminApi:
    """All workbench routes. Permission checks happen here, not in a client."""

    COOKIE_PATH = "/api/v1/admin"

    @staticmethod
    def _days(day_from: str | None, day_to: str | None):
        try:
            return ShanghaiDay.bounds(day_from, day_to)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD") from exc

    @staticmethod
    def register(app: FastAPI, get_session, clock, notifier, grant_writer) -> None:
        settings = get_settings()
        secure_cookie = settings.app_env not in {"local", "test"}

        def current_staff(
            request: Request,
            session: Session = Depends(get_session),
        ) -> StaffAccount:
            account = StaffSessions.resolve(session, RequestToken.read(request), clock())
            if account is None:
                raise HTTPException(status_code=401, detail="authentication required")
            return account

        def require_admin(account: StaffAccount = Depends(current_staff)) -> StaffAccount:
            if account.role != "admin":
                raise HTTPException(status_code=403, detail="admin only")
            return account

        def writer(session: Session, account: StaffAccount, request: Request | None) -> AuditWriter:
            address = None if request is None else ClientAddress.of(request)
            return AuditWriter(session, clock(), account, address)

        @app.post("/api/v1/admin/auth/login")
        def login(
            payload: LoginIn,
            request: Request,
            response: Response,
            session: Session = Depends(get_session),
        ):
            now = clock()
            audit = AuditWriter(session, now, None, ClientAddress.of(request))
            try:
                token, account = StaffAuthenticator(session, now, audit).login(
                    payload.username,
                    payload.password,
                    ClientAddress.of(request),
                )
            except WorkbenchError as exc:
                session.commit()
                raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
            response.set_cookie(
                key=StaffSessions.COOKIE_NAME,
                value=token,
                httponly=True,
                samesite="lax",
                secure=secure_cookie,
                path=AdminApi.COOKIE_PATH,
                max_age=int(StaffSessions.TTL.total_seconds()),
            )
            return {
                "token": token,
                "token_type": "bearer",
                "role": account.role,
                "username": account.username,
                "expires_at": (now + StaffSessions.TTL).isoformat(),
            }

        @app.post("/api/v1/admin/auth/logout")
        def logout(
            request: Request,
            response: Response,
            account: StaffAccount = Depends(current_staff),
            session: Session = Depends(get_session),
        ):
            now = clock()
            StaffSessions.revoke(session, RequestToken.read(request), now)
            writer(session, account, request).write(
                "staff_session",
                account.id,
                "logout",
                None,
                {"username": account.username},
            )
            response.delete_cookie(StaffSessions.COOKIE_NAME, path=AdminApi.COOKIE_PATH)
            return {"status": "ok"}

        @app.get("/api/v1/admin/auth/me")
        def me(account: StaffAccount = Depends(current_staff)):
            return {"id": account.id, "username": account.username, "role": account.role}

        @app.post("/api/v1/admin/staff", status_code=201)
        def create_staff(
            payload: StaffCreateIn,
            request: Request,
            account: StaffAccount = Depends(require_admin),
            session: Session = Depends(get_session),
        ):
            def action():
                created = StaffDirectory.create(
                    session,
                    payload.username,
                    payload.password,
                    payload.role,
                    clock(),
                )
                writer(session, account, request).write(
                    "staff_account",
                    created.id,
                    "create_staff",
                    None,
                    {"username": created.username, "role": created.role},
                )
                return AdminApi._staff(created)

            return Endpoint.invoke(action)

        @app.get("/api/v1/admin/staff")
        def list_staff(
            _account: StaffAccount = Depends(require_admin),
            session: Session = Depends(get_session),
        ):
            rows = session.scalars(select(StaffAccount).order_by(StaffAccount.username)).all()
            return [AdminApi._staff(row) for row in rows]

        @app.post("/api/v1/admin/staff/{account_id}/unlock")
        def unlock_staff(
            account_id: str,
            request: Request,
            account: StaffAccount = Depends(require_admin),
            session: Session = Depends(get_session),
        ):
            def action():
                audit = writer(session, account, request)
                unlocked = StaffAuthenticator(session, clock(), audit).unlock(account_id, account)
                return AdminApi._staff(unlocked)

            return Endpoint.invoke(action)

        @app.get("/api/v1/admin/audit-logs")
        def audit_logs(
            limit: int = Query(default=100, ge=1, le=500),
            actor: str | None = None,
            object_type: str | None = None,
            q: str | None = None,
            created_from: datetime | None = None,
            created_to: datetime | None = None,
            day_from: str | None = None,
            day_to: str | None = None,
            offset: int = Query(default=0, ge=0),
            paged: bool = False,
            _account: StaffAccount = Depends(require_admin),
            session: Session = Depends(get_session),
        ):
            opened, closed = AdminApi._days(day_from, day_to)
            rows, total = AuditQuery.latest(
                session,
                limit,
                actor=actor,
                object_type=object_type,
                query=q,
                created_from=opened or AwareInstant.parse(created_from),
                created_before=closed,
                created_to=None if closed is not None else AwareInstant.parse(created_to),
                offset=offset,
            )
            if paged:
                return {"items": rows, "total": total}
            return rows

        @app.get("/api/v1/admin/evidence-requests")
        def list_evidence(
            status: str | None = None,
            request_type: str | None = Query(default=None, alias="type"),
            created_from: datetime | None = None,
            created_to: datetime | None = None,
            day_from: str | None = None,
            day_to: str | None = None,
            vip_first: bool = True,
            _account: StaffAccount = Depends(current_staff),
            session: Session = Depends(get_session),
        ):
            now = clock()
            opened, closed = AdminApi._days(day_from, day_to)
            EvidenceOverdueScanner(session, notifier, lambda: now).scan()
            return EvidenceList(session, now).query(
                status=status,
                request_type=request_type,
                created_from=opened or AwareInstant.parse(created_from),
                created_to=None if closed is not None else AwareInstant.parse(created_to),
                created_before=closed,
                vip_first=vip_first,
                reveal_phone=_account.role == "admin",
            )

        @app.post("/api/v1/admin/evidence-requests", status_code=201)
        def create_evidence(
            payload: EvidenceCreateIn,
            request: Request,
            account: StaffAccount = Depends(current_staff),
            session: Session = Depends(get_session),
        ):
            def action():
                now = clock()
                row = EvidenceWorkflow(session, notifier, writer(session, account, request)).create(
                    payload.user_id,
                    payload.request_type,
                    now,
                    payload.application_number,
                )
                return AdminApi._evidence_detail(session, row, account, now)

            return Endpoint.invoke(action)

        @app.get("/api/v1/admin/evidence-requests/by-number/{application_number}")
        def evidence_by_number(
            application_number: str,
            account: StaffAccount = Depends(current_staff),
            session: Session = Depends(get_session),
        ):
            row = EvidenceLookup.by_application_number(session, application_number)
            if row is None:
                raise HTTPException(status_code=404, detail="evidence request not found")
            return AdminApi._evidence_detail(session, row, account, clock())

        @app.get("/api/v1/admin/evidence-requests/{request_id}")
        def evidence_detail(
            request_id: str,
            account: StaffAccount = Depends(current_staff),
            session: Session = Depends(get_session),
        ):
            found = session.get(EvidenceRequest, request_id)
            if found is None:
                raise HTTPException(status_code=404, detail="evidence request not found")
            return AdminApi._evidence_detail(session, found, account, clock())

        @app.post("/api/v1/admin/evidence-requests/{request_id}/accept")
        def accept_evidence(
            request_id: str,
            request: Request,
            account: StaffAccount = Depends(current_staff),
            session: Session = Depends(get_session),
        ):
            def action():
                now = clock()
                row = EvidenceWorkflow(session, notifier, writer(session, account, request)).accept(
                    request_id, now
                )
                return AdminApi._evidence_detail(session, row, account, now)

            return Endpoint.invoke(action)

        @app.post("/api/v1/admin/evidence-requests/{request_id}/quote")
        def quote_evidence(
            request_id: str,
            payload: AmountIn,
            request: Request,
            account: StaffAccount = Depends(current_staff),
            session: Session = Depends(get_session),
        ):
            def action():
                now = clock()
                row = EvidenceWorkflow(
                    session, notifier, writer(session, account, request)
                ).set_quote(request_id, payload.amount, now)
                return AdminApi._evidence_detail(session, row, account, now)

            return Endpoint.invoke(action)

        @app.post("/api/v1/admin/evidence-requests/{request_id}/confirm-payment")
        def pay_evidence(
            request_id: str,
            payload: AmountIn,
            request: Request,
            account: StaffAccount = Depends(current_staff),
            session: Session = Depends(get_session),
        ):
            def action():
                now = clock()
                flow = EvidenceWorkflow(session, notifier, writer(session, account, request))
                row = flow.confirm_payment(request_id, payload.amount, now)
                return AdminApi._evidence_detail(session, row, account, now)

            return Endpoint.invoke(action)

        @app.post("/api/v1/admin/evidence-requests/{request_id}/deliver")
        def deliver_evidence(
            request_id: str,
            payload: DeliveryIn,
            request: Request,
            account: StaffAccount = Depends(current_staff),
            session: Session = Depends(get_session),
        ):
            def action():
                now = clock()
                flow = EvidenceWorkflow(session, notifier, writer(session, account, request))
                row = flow.deliver(request_id, payload.method, payload.note, now)
                return AdminApi._evidence_detail(session, row, account, now)

            return Endpoint.invoke(action)

        @app.post("/api/v1/admin/evidence-requests/{request_id}/fail")
        def fail_evidence(
            request_id: str,
            payload: FailIn,
            request: Request,
            account: StaffAccount = Depends(current_staff),
            session: Session = Depends(get_session),
        ):
            def action():
                now = clock()
                flow = EvidenceWorkflow(session, notifier, writer(session, account, request))
                row = flow.fail(
                    request_id,
                    payload.reason,
                    now,
                    payload.refund_method,
                    payload.refund_amount,
                    payload.refund_note,
                )
                if payload.and_close:
                    row = flow.close(row.id, now)
                return AdminApi._evidence_detail(session, row, account, now)

            return Endpoint.invoke(action)

        @app.post("/api/v1/admin/evidence-requests/{request_id}/complete")
        def complete_evidence(
            request_id: str,
            request: Request,
            account: StaffAccount = Depends(current_staff),
            session: Session = Depends(get_session),
        ):
            def action():
                now = clock()
                flow = EvidenceWorkflow(session, notifier, writer(session, account, request))
                row = flow.complete(request_id, now)
                return AdminApi._evidence_detail(session, row, account, now)

            return Endpoint.invoke(action)

        @app.post("/api/v1/admin/evidence-requests/{request_id}/close")
        def close_evidence(
            request_id: str,
            request: Request,
            account: StaffAccount = Depends(current_staff),
            session: Session = Depends(get_session),
        ):
            def action():
                now = clock()
                flow = EvidenceWorkflow(session, notifier, writer(session, account, request))
                row = flow.close(request_id, now)
                return AdminApi._evidence_detail(session, row, account, now)

            return Endpoint.invoke(action)

        @app.post("/api/v1/admin/evidence-requests/{request_id}/cancel")
        def cancel_evidence(
            request_id: str,
            request: Request,
            account: StaffAccount = Depends(current_staff),
            session: Session = Depends(get_session),
        ):
            def action():
                now = clock()
                flow = EvidenceWorkflow(session, notifier, writer(session, account, request))
                row = flow.cancel(request_id, now)
                return AdminApi._evidence_detail(session, row, account, now)

            return Endpoint.invoke(action)

        @app.post("/api/v1/admin/evidence-requests/{request_id}/notes", status_code=201)
        def add_note(
            request_id: str,
            payload: NoteIn,
            request: Request,
            account: StaffAccount = Depends(current_staff),
            session: Session = Depends(get_session),
        ):
            def action():
                now = clock()
                flow = EvidenceWorkflow(session, notifier, writer(session, account, request))
                note = flow.add_note(request_id, account, payload.text, now)
                return EvidenceView.note(note)

            return Endpoint.invoke(action)

        @app.get("/api/v1/admin/users")
        def list_users(
            phone: str | None = None,
            q: str | None = None,
            account: StaffAccount = Depends(current_staff),
            session: Session = Depends(get_session),
        ):
            query = (q or phone or "").strip()
            if not query:
                raise HTTPException(status_code=400, detail="phone is required")

            def action():
                rows = MemberDirectory(session).by_contact(query)
                return [UserView.render(row, reveal_phone=account.role == "admin") for row in rows]

            return Endpoint.invoke(action)

        @app.get("/api/v1/admin/users/{user_id}")
        def get_user(
            user_id: str,
            account: StaffAccount = Depends(current_staff),
            session: Session = Depends(get_session),
        ):
            def action():
                user = MemberDirectory(session).get(user_id)
                return UserProfile.detail(
                    session,
                    user,
                    reveal_phone=account.role == "admin",
                    now=clock(),
                )

            return Endpoint.invoke(action)

        @app.post("/api/v1/admin/users/{user_id}/ban")
        def ban_user(
            user_id: str,
            payload: BanIn,
            request: Request,
            account: StaffAccount = Depends(require_admin),
            session: Session = Depends(get_session),
        ):
            def action():
                now = clock()
                user = MemberAdmin(session, writer(session, account, request)).ban(
                    user_id,
                    payload.type,
                    payload.reason,
                    AwareInstant.parse(payload.until),
                    now,
                )
                return UserView.render(user, reveal_phone=True)

            return Endpoint.invoke(action)

        @app.post("/api/v1/admin/users/{user_id}/unban")
        def unban_user(
            user_id: str,
            request: Request,
            account: StaffAccount = Depends(require_admin),
            session: Session = Depends(get_session),
        ):
            def action():
                user = MemberAdmin(session, writer(session, account, request)).unban(user_id)
                return UserView.render(user, reveal_phone=True)

            return Endpoint.invoke(action)

        @app.post("/api/v1/admin/users/{user_id}/membership", status_code=201)
        def grant_membership(
            user_id: str,
            payload: MembershipIn,
            request: Request,
            account: StaffAccount = Depends(require_admin),
            session: Session = Depends(get_session),
        ):
            def action():
                grant = MemberAdmin(session, writer(session, account, request)).grant(
                    user_id,
                    payload.tier,
                    payload.days,
                    payload.reason,
                    clock(),
                )
                return {
                    "id": grant.id,
                    "user_id": grant.user_id,
                    "tier": grant.kind,
                    "source": grant.source,
                    "expires_at": grant.expires_at.isoformat(),
                }

            return Endpoint.invoke(action)

        @app.get("/api/v1/admin/orders")
        def list_orders(
            account: StaffAccount = Depends(current_staff),
            session: Session = Depends(get_session),
        ):
            reveal = account.role == "admin"
            return [
                OrderView.render(order, phone, reveal_phone=reveal, issuance=issuance)
                for order, phone, issuance in OrderDirectory(session).list_orders()
            ]

        @app.get("/api/v1/admin/orders/export")
        def export_orders(
            _account: StaffAccount = Depends(require_admin),
            session: Session = Depends(get_session),
        ):
            rows = [
                OrderView.render(order, phone, reveal_phone=True, issuance=issuance)
                for order, phone, issuance in OrderDirectory(session).list_orders()
            ]
            return Response(
                content=OrderExport.csv(rows),
                media_type="text/csv",
                headers={"Content-Disposition": "attachment; filename=orders.csv"},
            )

        @app.get("/api/v1/admin/orders/by-number/{order_number}")
        def order_by_number(
            order_number: str,
            account: StaffAccount = Depends(current_staff),
            session: Session = Depends(get_session),
        ):
            order = OrderLookup.by_order_number(session, order_number)
            if order is None:
                raise HTTPException(status_code=404, detail="order not found")
            _order, phone, issuance = OrderDirectory(session).one(order)
            return OrderView.render(
                order, phone, reveal_phone=account.role == "admin", issuance=issuance
            )

        @app.get("/api/v1/admin/orders/{order_id}")
        def get_order(
            order_id: str,
            account: StaffAccount = Depends(current_staff),
            session: Session = Depends(get_session),
        ):
            def action():
                order = OrderLookup.by_id(session, order_id)
                _order, phone, issuance = OrderDirectory(session).one(order)
                return OrderView.render(
                    order, phone, reveal_phone=account.role == "admin", issuance=issuance
                )

            return Endpoint.invoke(action)

        @app.post("/api/v1/admin/orders/{order_id}/reissue")
        def reissue_order(
            order_id: str,
            request: Request,
            payload: ReissueIn | None = Body(default=None),
            account: StaffAccount = Depends(current_staff),
            session: Session = Depends(get_session),
        ):
            del payload

            def action():
                order = OrderLookup.by_id(session, order_id)
                before = order.status
                issuer = EntitlementIssuer(
                    session,
                    grant_writer or OrderGrantWriter(),
                    notifier,
                    clock(),
                )
                issuance = issuer.reissue(order)
                OrderAudit.reissue(writer(session, account, request), order, before, issuance)
                _order, phone, current = OrderDirectory(session).one(order)
                body = OrderView.render(
                    order, phone, reveal_phone=account.role == "admin", issuance=current
                )
                body["entitlement_id"] = issuance.entitlement_id
                body["issuance_status"] = issuance.status
                return body

            return Endpoint.invoke(action)

        @app.post("/api/v1/admin/orders/{order_id}/refund")
        def refund_order(
            order_id: str,
            payload: RefundIn,
            request: Request,
            account: StaffAccount = Depends(require_admin),
            session: Session = Depends(get_session),
        ):
            def action():
                order = OrderLookup.by_id(session, order_id)
                OrderRefund(session, writer(session, account, request), clock()).refund(
                    order, payload.reason
                )
                _order, phone, issuance = OrderDirectory(session).one(order)
                return OrderView.render(order, phone, reveal_phone=True, issuance=issuance)

            return Endpoint.invoke(action)

        @app.get("/api/v1/admin/staff/locks")
        def staff_locks(
            _account: StaffAccount = Depends(require_admin),
            session: Session = Depends(get_session),
        ):
            return StaffDirectory.active_locks(session, clock())

        @app.get("/api/v1/admin/invite-codes/stats")
        def invite_stats(
            _account: StaffAccount = Depends(require_admin),
            session: Session = Depends(get_session),
        ):
            return InviteStats.channels(session, clock())

        @app.get("/api/v1/admin/invite-codes/export")
        def export_invites(
            _account: StaffAccount = Depends(require_admin),
            session: Session = Depends(get_session),
        ):
            rows = InviteBook(session, writer(session, _account, None), clock()).list_codes(
                status=None, channel=None, query=None
            )
            return Response(
                content=AdminApi._invite_csv(rows),
                media_type="text/csv",
                headers={"Content-Disposition": "attachment; filename=invites.csv"},
            )

        @app.get("/api/v1/admin/invite-codes")
        def list_invites(
            status: str | None = None,
            channel: str | None = None,
            q: str | None = None,
            account: StaffAccount = Depends(require_admin),
            session: Session = Depends(get_session),
        ):
            return InviteBook(session, writer(session, account, None), clock()).list_codes(
                status=status, channel=channel, query=q
            )

        @app.post("/api/v1/admin/invite-codes", status_code=201)
        def create_invites(
            payload: InviteBatchIn,
            request: Request,
            account: StaffAccount = Depends(require_admin),
            session: Session = Depends(get_session),
        ):
            def action():
                now = clock()
                rows = InviteBook(session, writer(session, account, request), now).generate(
                    payload.count,
                    payload.channel,
                    AwareInstant.parse(payload.expires_at),
                    account,
                )
                return [InviteView.render(row, now, None, account.username) for row in rows]

            return Endpoint.invoke(action)

        @app.post("/api/v1/admin/invite-codes/{code_id}/void")
        def void_invite(
            code_id: str,
            request: Request,
            account: StaffAccount = Depends(require_admin),
            session: Session = Depends(get_session),
        ):
            def action():
                now = clock()
                row = InviteBook(session, writer(session, account, request), now).void(
                    code_id, account
                )
                phone = None
                if row.redeemed_by:
                    user = session.get(User, row.redeemed_by)
                    phone = None if user is None else user.phone
                staff = session.get(StaffAccount, row.created_by)
                creator = "" if staff is None else staff.username
                return InviteView.render(row, now, phone, creator)

            return Endpoint.invoke(action)

        @app.get("/api/v1/admin/content/splash")
        def get_splash(
            _account: StaffAccount = Depends(require_admin),
            session: Session = Depends(get_session),
        ):
            book = SplashBook(session, writer(session, _account, None), clock())
            return SplashView.render(book.get())

        @app.put("/api/v1/admin/content/splash")
        def save_splash(
            payload: SplashIn,
            request: Request,
            account: StaffAccount = Depends(require_admin),
            session: Session = Depends(get_session),
        ):
            def action():
                row = SplashBook(session, writer(session, account, request), clock()).save(
                    enabled=payload.enabled,
                    image_name=payload.image_name,
                    jump_label=payload.jump_label,
                    slogan=payload.slogan,
                )
                return SplashView.render(row)

            return Endpoint.invoke(action)

        @app.get("/api/v1/admin/content/announcements")
        def list_announcements(
            account: StaffAccount = Depends(require_admin),
            session: Session = Depends(get_session),
        ):
            return AnnouncementBook(session, writer(session, account, None), clock()).list_rows()

        @app.post("/api/v1/admin/content/announcements", status_code=201)
        def save_announcement(
            payload: AnnouncementIn,
            request: Request,
            account: StaffAccount = Depends(require_admin),
            session: Session = Depends(get_session),
        ):
            def action():
                row = AnnouncementBook(session, writer(session, account, request), clock()).save(
                    title=payload.title,
                    body=payload.body,
                    button_text=payload.button_text,
                    jump_label=payload.jump_label,
                    starts_at=AwareInstant.parse(payload.starts_at),
                    ends_at=AwareInstant.parse(payload.ends_at),
                    frequency=payload.frequency,
                    online=payload.online,
                    announcement_id=payload.id,
                )
                return AnnouncementView.render(row, clock())

            return Endpoint.invoke(action)

        @app.post("/api/v1/admin/content/announcements/{announcement_id}/online")
        def announcement_online(
            announcement_id: str,
            payload: OnlineIn,
            request: Request,
            account: StaffAccount = Depends(require_admin),
            session: Session = Depends(get_session),
        ):
            def action():
                book = AnnouncementBook(session, writer(session, account, request), clock())
                row = book.set_online(announcement_id, payload.online)
                return AnnouncementView.render(row, clock())

            return Endpoint.invoke(action)

        @app.get("/api/v1/admin/content/examples")
        def list_examples(
            account: StaffAccount = Depends(require_admin),
            session: Session = Depends(get_session),
        ):
            return ExampleBook(session, writer(session, account, None), clock()).list_rows()

        @app.get("/api/v1/admin/content/programs")
        def list_programs(
            _account: StaffAccount = Depends(require_admin),
            session: Session = Depends(get_session),
        ):
            return ProgramCatalog.list_programs(session)

        @app.post("/api/v1/admin/content/examples", status_code=201)
        def add_example(
            payload: ExampleIn,
            request: Request,
            account: StaffAccount = Depends(require_admin),
            session: Session = Depends(get_session),
        ):
            def action():
                row = ExampleBook(session, writer(session, account, request), clock()).add(
                    payload.program_name, payload.record_id
                )
                return ExampleView.render(row)

            return Endpoint.invoke(action)

        @app.post("/api/v1/admin/content/examples/{example_id}/online")
        def example_online(
            example_id: str,
            payload: OnlineIn,
            request: Request,
            account: StaffAccount = Depends(require_admin),
            session: Session = Depends(get_session),
        ):
            def action():
                row = ExampleBook(session, writer(session, account, request), clock()).set_online(
                    example_id, payload.online
                )
                return ExampleView.render(row)

            return Endpoint.invoke(action)

        @app.post("/api/v1/admin/content/examples/{example_id}/move")
        def move_example(
            example_id: str,
            payload: ExampleMoveIn,
            request: Request,
            account: StaffAccount = Depends(require_admin),
            session: Session = Depends(get_session),
        ):
            def action():
                row = ExampleBook(session, writer(session, account, request), clock()).move(
                    example_id, payload.direction
                )
                return ExampleView.render(row)

            return Endpoint.invoke(action)

        @app.get("/api/v1/admin/index/status")
        def index_status(
            _account: StaffAccount = Depends(require_admin),
            session: Session = Depends(get_session),
        ):
            return IndexStatus.payload(session, clock())

        @app.post("/api/v1/admin/index/import")
        def import_index(
            payload: ImportIn,
            request: Request,
            account: StaffAccount = Depends(require_admin),
            session: Session = Depends(get_session),
        ):
            def action():
                try:
                    run = IndexJob(session, CsvTaskExportSource(payload.csv), notifier, clock).run()
                except IndexRunBusy:
                    raise WorkbenchError(409, "index run is already active") from None
                writer(session, account, request).write(
                    "index_run",
                    str(run.id),
                    "import_index",
                    None,
                    {
                        "status": run.status,
                        "new_rows": run.new_rows,
                        "error": run.error_summary,
                    },
                )
                return IndexStatus.payload(session, clock())

            return Endpoint.invoke(action)

    @staticmethod
    def _staff(account: StaffAccount) -> dict:
        return {
            "id": account.id,
            "username": account.username,
            "role": account.role,
            "created_at": account.created_at.isoformat(),
        }

    @staticmethod
    def _evidence_detail(session: Session, row, account: StaffAccount, now: datetime) -> dict:
        user = session.get(User, row.user_id)
        phone = None if user is None else user.phone
        notes = EvidenceWorkflow.notes_for(session, row.id)
        kinds = set(
            session.scalars(
                select(Entitlement.kind).where(
                    Entitlement.user_id == row.user_id,
                    Entitlement.kind.in_(("vip", "regular")),
                    Entitlement.expires_at > now,
                )
            ).all()
        )
        tier = membership_kind(kinds)
        body = EvidenceView.detail(
            row,
            notes,
            overdue=EvidenceOverdue.is_overdue(row, now),
            vip=tier == "vip",
            phone=phone,
            reveal_phone=account.role == "admin",
        )
        body["membership"] = tier
        return body

    @staticmethod
    def _invite_csv(rows: list[dict]) -> str:
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        header = (
            "code",
            "channel",
            "status",
            "created_at",
            "expires_at",
            "redeemed_phone",
            "redeemed_at",
            "voided_at",
        )
        writer.writerow(header)
        for row in rows:
            writer.writerow([row.get(name) or "" for name in header])
        return buffer.getvalue()
