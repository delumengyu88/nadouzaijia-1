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
from nadou.models import User
from nadou.payments.api import PaymentApi
from nadou.public_config import PublicConfig
from nadou.public_home import PublicHome
from nadou.schemas import SearchOut
from nadou.search import EmptyQuery, ProgramSearch, UnknownUser
from nadou.signed_url import FakeSignedUrlProvider, SignedUrlProvider

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
    urls = urls or FakeSignedUrlProvider()
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
    UserEvidence.register(app, get_session, clock, alerts)
    PaymentApi.register(app, get_session, clock, alerts, grant_writer)
    # Experience-build only. Delete this line with src/nadou/devtools before review.
    register_trial_tools(app, get_session, clock)
    AdminApi.register(app, get_session, clock, alerts, grant_writer)

    @app.get("/api/v1/queries/{query_id}/export")
    def export_query(
        query_id: str,
        session: Session = Depends(get_session, scope="function"),
        user: User = Depends(current_user),
    ):
        # Imported here so the workbench process does not load the image stack at startup.
        from nadou.frames import GeneratedFrameReader, SignedDerivedFrameSource
        from nadou.query_export import ExportForbidden, ExportLimits, QueryExport, UnknownQuery

        frame_source = frames or SignedDerivedFrameSource(urls, GeneratedFrameReader())
        max_bytes = ExportLimits.MAX_BYTES if export_max_bytes is None else export_max_bytes
        try:
            exported = QueryExport(session, frame_source, clock(), max_bytes).export(
                query_id, user.id
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
        return Response(
            content=exported.body,
            media_type=exported.media_type,
            headers={"Content-Disposition": exported.disposition()},
        )

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
