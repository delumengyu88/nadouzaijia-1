from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, StringConstraints
from sqlalchemy.orm import Session

from nadou.admin.api import AdminApi
from nadou.admin.audit import AuditWriter
from nadou.admin.errors import WorkbenchError
from nadou.admin.invites import InviteRedemption
from nadou.admin.notify import NotifierFactory
from nadou.admin.schemas import InviteRedeemIn
from nadou.auth import UserSessions, WeChatLogin, require_active, user_profile
from nadou.config import get_settings
from nadou.db import Database
from nadou.devtools.trial_tools import register_trial_tools
from nadou.evidence_user import UserEvidence
from nadou.invite_state import InviteState
from nadou.models import User
from nadou.payments.api import PaymentApi
from nadou.public_config import PublicConfig
from nadou.public_home import PublicHome
from nadou.query_records import QueryNotFound, QueryRecords, QueryRejected, ResultSummary
from nadou.record_frames import RecordFrames
from nadou.schemas import BatchSearchIn, SearchOut
from nadou.search import EmptyQuery, ProgramSearch, UnknownUser
from nadou.signed_url import FakeSignedUrlProvider, SignedUrlProvider
from nadou.user_orders import UserOrders

_BEIJING = ZoneInfo("Asia/Shanghai")


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
        return {
            "token": token,
            "token_type": "bearer",
            "expires_at": (now + UserSessions.TTL).isoformat(),
            "user": user_profile(session, user, now),
        }

    @app.get("/api/v1/auth/me")
    def me(
        user: User = Depends(current_user),
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
        user: User = Depends(current_user),
    ):
        try:
            return ProgramSearch(session, urls, clock(), frames_ready=frames is not None).search(
                name, user.id
            )
        except EmptyQuery:
            raise HTTPException(status_code=400, detail="name is required") from None
        except UnknownUser:
            raise HTTPException(status_code=404, detail="user not found") from None

    @app.post(
        "/api/v1/programs/search/batch",
        response_model=list[SearchOut],
        response_model_exclude_none=True,
    )
    def search_programs(
        payload: BatchSearchIn,
        session: Session = Depends(get_session, scope="function"),
        user: User = Depends(current_user),
    ):
        now = clock()
        try:
            programs = (
                QueryRecords.batch_programs(session, user.id, payload.query_id, payload.names)
                if payload.query_id
                else {}
            )
            results, matches = ProgramSearch(
                session, urls, now, frames_ready=frames is not None
            ).search_many(payload.names, user.id)
            for name, program in programs.items():
                program.resolution, program.detail, program.matched_name = (
                    ResultSummary.assess_pairs(name, matches.get(name, []), now)
                )
            return results
        except EmptyQuery:
            raise HTTPException(400, "name is required") from None
        except QueryNotFound:
            raise HTTPException(404, "query not found") from None
        except QueryRejected as exc:
            raise HTTPException(400, exc.detail) from None

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
    QueryRecords.register(app, get_session, clock)
    RecordFrames.register(app, get_session, clock, frames, current_user)
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
        headers["X-Nadou-Export-Scope"] = "program" if program_name is not None else "query"
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
