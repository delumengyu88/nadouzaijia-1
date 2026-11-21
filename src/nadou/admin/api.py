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
from nadou.admin.errors import WorkbenchError
from nadou.admin.evidence import (
    EvidenceList,
    EvidenceLookup,
    EvidenceOverdue,
    EvidenceOverdueScanner,
    EvidenceView,
    EvidenceWorkflow,
)
from nadou.admin.indexing import IndexStatus
from nadou.admin.members import MemberAdmin, MemberDirectory, UserView
from nadou.admin.orders import (
    EntitlementIssuer,
    OrderAudit,
    OrderDirectory,
    OrderExport,
    OrderGrantWriter,
    OrderLookup,
    OrderView,
)
from nadou.admin.schemas import (
    AmountIn,
    BanIn,
    DeliveryIn,
    EvidenceCreateIn,
    FailIn,
    LoginIn,
    MembershipIn,
    NoteIn,
    ReissueIn,
    StaffCreateIn,
)
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

        def writer(session: Session, account: StaffAccount, request: Request) -> AuditWriter:
            return AuditWriter(session, clock(), account, ClientAddress.of(request))

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
            _account: StaffAccount = Depends(require_admin),
            session: Session = Depends(get_session),
        ):
            return AuditQuery.latest(session, limit)

        @app.get("/api/v1/admin/evidence-requests")
        def list_evidence(
            status: str | None = None,
            request_type: str | None = Query(default=None, alias="type"),
            created_from: datetime | None = None,
            created_to: datetime | None = None,
            vip_first: bool = True,
            _account: StaffAccount = Depends(current_staff),
            session: Session = Depends(get_session),
        ):
            now = clock()
            EvidenceOverdueScanner(session, notifier, lambda: now).scan()
            return EvidenceList(session, now).query(
                status=status,
                request_type=request_type,
                created_from=AwareInstant.parse(created_from),
                created_to=AwareInstant.parse(created_to),
                vip_first=vip_first,
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
                ).set_quote(request_id, payload.amount)
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
                )
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
            account: StaffAccount = Depends(current_staff),
            session: Session = Depends(get_session),
        ):
            if not phone:
                raise HTTPException(status_code=400, detail="phone is required")

            def action():
                rows = MemberDirectory(session).by_phone(phone)
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
                return UserView.render(user, reveal_phone=account.role == "admin")

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
                OrderView.render(order, phone, reveal_phone=reveal)
                for order, phone in OrderDirectory(session).list_orders()
            ]

        @app.get("/api/v1/admin/orders/export")
        def export_orders(
            _account: StaffAccount = Depends(require_admin),
            session: Session = Depends(get_session),
        ):
            rows = [
                OrderView.render(order, phone, reveal_phone=True)
                for order, phone in OrderDirectory(session).list_orders()
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
            _order, phone = OrderDirectory(session).one(order)
            return OrderView.render(order, phone, reveal_phone=account.role == "admin")

        @app.get("/api/v1/admin/orders/{order_id}")
        def get_order(
            order_id: str,
            account: StaffAccount = Depends(current_staff),
            session: Session = Depends(get_session),
        ):
            def action():
                order = OrderLookup.by_id(session, order_id)
                _order, phone = OrderDirectory(session).one(order)
                return OrderView.render(order, phone, reveal_phone=account.role == "admin")

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
                _order, phone = OrderDirectory(session).one(order)
                body = OrderView.render(order, phone, reveal_phone=account.role == "admin")
                body["entitlement_id"] = issuance.entitlement_id
                body["issuance_status"] = issuance.status
                return body

            return Endpoint.invoke(action)

        @app.get("/api/v1/admin/index/status")
        def index_status(
            _account: StaffAccount = Depends(require_admin),
            session: Session = Depends(get_session),
        ):
            return IndexStatus.payload(session)

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
        vip = (
            session.scalar(
                select(Entitlement.id).where(
                    Entitlement.user_id == row.user_id,
                    Entitlement.kind == "vip",
                    Entitlement.expires_at > now,
                )
            )
            is not None
        )
        return EvidenceView.detail(
            row,
            notes,
            overdue=EvidenceOverdue.is_overdue(row, now),
            vip=vip,
            phone=phone,
            reveal_phone=account.role == "admin",
        )
