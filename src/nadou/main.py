from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Annotated
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlalchemy.orm import Session

from nadou.admin.api import AdminApi
from nadou.admin.audit import AuditWriter
from nadou.admin.errors import WorkbenchError
from nadou.admin.invites import InviteRedemption
from nadou.admin.notify import NotifierFactory
from nadou.admin.schemas import InviteRedeemIn
from nadou.auth import GuestIssueLimit, UserSessions, WeChatLogin, require_active, user_profile
from nadou.config import get_settings
from nadou.db import Database
from nadou.devtools.trial_tools import register_trial_tools
from nadou.evidence_user import UserEvidence
from nadou.invite_state import InviteState
from nadou.models import User
from nadou.payments.api import PaymentApi
from nadou.public_config import PublicConfig
from nadou.public_home import PublicHome
from nadou.query_records import QueryRecords
from nadou.schemas import SearchOut
from nadou.search import EmptyQuery, ProgramSearch, UnknownUser
from nadou.signed_url import FakeSignedUrlProvider, SignedUrlProvider
from nadou.user_orders import UserOrders

_BEIJING = ZoneInfo("Asia/Shanghai")


class GuestSessionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WeChatLoginIn(BaseModel):
    code: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=512)]


def create_app(
    database: Database | None = None,
    urls: SignedUrlProvider | None = None,
    now: Callable[[], datetime] | None = None,
    notifier=None,
    grant_writer=None,
    frames=None,
    export_max_bytes: int | None = None,
) -> FastAPI:
    database = database or Database(get_settings().database_url)
    if get_settings().demo_data:
        from nadou.frames import GeneratedFrameReader, SignedDerivedFrameSource

        urls = urls or FakeSignedUrlProvider()
        frames = frames or SignedDerivedFrameSource(urls, GeneratedFrameReader())
    else:
        # The deployed app has no authorized asset reader or public image signer.
        urls = None
        frames = None
    clock = now or (lambda: datetime.now(_BEIJING))
    alerts = notifier if notifier is not None else NotifierFactory.from_settings(get_settings())
    app = FastAPI(title="那豆在架")

    # Function-scoped dependencies commit before sending success to the client.
    # Follow-up reads (login/me, payment/list) must see the committed transaction.
    def get_session():
        session = database.session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def current_user(
        authorization: str | None = Header(default=None),
        session: Session = Depends(get_session, scope="function"),
    ) -> User:
        return UserSessions.resolve(session, authorization, clock())

    def query_user(
        authorization: str | None = Header(default=None),
        session: Session = Depends(get_session, scope="function"),
    ) -> User:
        return UserSessions.resolve(session, authorization, clock(), allow_guest=True)

    issue_limit = GuestIssueLimit()

    @app.post("/api/v1/auth/guest/session")
    def guest_session(
        payload: GuestSessionIn,
        response: Response,
        authorization: str | None = Header(default=None),
        session: Session = Depends(get_session, scope="function"),
    ):
        now = clock()
        response.headers["Cache-Control"] = "no-store"
        if authorization is not None:
            user, row, token = UserSessions.credentials(session, authorization, now)
            expires = UserSessions.renew_guest(session, user, row, now)
            return UserSessions.response(session, user, token, expires, now)
        issue_limit.admit()
        user = User(id=str(uuid4()), is_guest=True, created_at=now)
        session.add(user)
        session.flush()
        token = UserSessions.issue(session, user, now)
        return UserSessions.response(session, user, token, now + UserSessions.GUEST_TTL, now)

    @app.post("/api/v1/auth/wechat/login")
    def wechat_login(
        payload: WeChatLoginIn,
        session: Session = Depends(get_session, scope="function"),
    ):
        openid = WeChatLogin.exchange(payload.code)
        now = clock()
        user = WeChatLogin.user(session, openid, now)
        require_active(user, now)
        token = UserSessions.issue(session, user, now)
        return UserSessions.response(session, user, token, now + UserSessions.TTL, now)

    @app.get("/api/v1/auth/me")
    def me(
        user: User = Depends(query_user),
        session: Session = Depends(get_session, scope="function"),
    ):
        return user_profile(session, user, clock())

    @app.get("/health")
    @app.get("/healthz")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get(
        "/api/v1/programs/search",
        response_model=SearchOut,
        response_model_exclude_none=True,
    )
    def search_program(
        name: str,
        session: Session = Depends(get_session, scope="function"),
        user: User = Depends(query_user),
    ):
        try:
            return ProgramSearch(session, urls, clock()).search(name, user.id)
        except EmptyQuery:
            raise HTTPException(status_code=400, detail="name is required") from None
        except UnknownUser:
            raise HTTPException(status_code=404, detail="user not found") from None

    @app.post("/api/v1/invite-codes/redeem")
    def redeem_invite(
        payload: InviteRedeemIn,
        session: Session = Depends(get_session, scope="function"),
        user: User = Depends(current_user),
    ):
        try:
            grant = InviteRedemption(
                session, AuditWriter(session, clock(), None, None), clock()
            ).redeem(payload.code, user.id)
        except WorkbenchError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
        return {
            "entitlement_id": grant.id,
            "tier": grant.kind,
            "source": grant.source,
            "expires_at": grant.expires_at.isoformat(),
        }

    PublicConfig.register(app, get_session)
    PublicHome.register(app, get_session, clock)
    InviteState.register(app, get_session, clock)
    UserEvidence.register(app, get_session, clock, alerts)
    QueryRecords.register(app, get_session, clock, query_user)
    PaymentApi.register(app, get_session, clock, alerts, grant_writer)
    UserOrders.register(app, get_session, clock)
    # Experience-build only. Delete this line with src/nadou/devtools before review.
    register_trial_tools(app, get_session, clock)
    AdminApi.register(app, get_session, clock, alerts, grant_writer)

    def export_response(query_id: str, program_name: str | None, session: Session, user: User):
        from nadou.query_export import ExportForbidden, ExportLimits, QueryExport, UnknownQuery

        max_bytes = ExportLimits.MAX_BYTES if export_max_bytes is None else export_max_bytes
        try:
            exported = QueryExport(session, frames, clock(), max_bytes).export(
                query_id, user.id, program_name=program_name
            )
        except UnknownUser:
            raise HTTPException(status_code=404, detail="user not found") from None
        except UnknownQuery:
            raise HTTPException(status_code=404, detail="query not found") from None
        except ExportForbidden:
            raise HTTPException(
                status_code=403,
                detail="export requires an active membership",
            ) from None
        headers = {"Content-Disposition": exported.disposition()}
        if program_name is not None:
            headers["X-Nadou-Export-Scope"] = "program"
        return Response(
            content=exported.body,
            media_type=exported.media_type,
            headers=headers,
        )

    @app.get("/api/v1/queries/{query_id}/export")
    def export_query(
        query_id: str,
        program_name: str | None = None,
        session: Session = Depends(get_session, scope="function"),
        user: User = Depends(current_user),
    ):
        return export_response(query_id, program_name, session, user)

    @app.get("/api/v1/queries/{query_id}/program-export")
    def export_program(
        query_id: str,
        program_name: str,
        session: Session = Depends(get_session, scope="function"),
        user: User = Depends(current_user),
    ):
        return export_response(query_id, program_name, session, user)

    AdminUi.mount(app)
    return app


class AdminUi:
    """Static workbench. No build step; the files ship with the package."""

    @staticmethod
    def root() -> Path:
        return Path(__file__).resolve().parent / "static" / "admin"

    @staticmethod
    def mount(app: FastAPI) -> None:
        directory = AdminUi.root()
        if not directory.is_dir():
            return
        app.mount("/admin", StaticFiles(directory=directory, html=True), name="admin-ui")


app = create_app()
