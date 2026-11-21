from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from nadou.admin.api import AdminApi
from nadou.admin.audit import AuditWriter
from nadou.admin.errors import WorkbenchError
from nadou.admin.invites import InviteRedemption
from nadou.admin.notify import NotifierFactory
from nadou.admin.schemas import InviteRedeemIn
from nadou.config import get_settings
from nadou.db import Database
from nadou.schemas import SearchOut
from nadou.search import EmptyQuery, ProgramSearch, UnknownUser
from nadou.signed_url import FakeSignedUrlProvider, SignedUrlProvider

_BEIJING = ZoneInfo("Asia/Shanghai")


def create_app(
    database: Database | None = None,
    urls: SignedUrlProvider | None = None,
    now: Callable[[], datetime] | None = None,
    notifier=None,
    grant_writer=None,
) -> FastAPI:
    database = database or Database(get_settings().database_url)
    urls = urls or FakeSignedUrlProvider()
    clock = now or (lambda: datetime.now(_BEIJING))
    alerts = notifier if notifier is not None else NotifierFactory.from_settings(get_settings())
    app = FastAPI(title="那豆在架")

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

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get(
        "/api/v1/programs/search",
        response_model=SearchOut,
        response_model_exclude_none=True,
    )
    def search_program(
        name: str,
        session: Session = Depends(get_session),
        x_user_id: str | None = Header(default=None),
    ):
        caller = x_user_id.strip() if x_user_id else None
        if not caller:
            caller = None
        try:
            return ProgramSearch(session, urls, clock()).search(name, caller)
        except EmptyQuery:
            raise HTTPException(status_code=400, detail="name is required") from None
        except UnknownUser:
            raise HTTPException(status_code=404, detail="user not found") from None

    @app.post("/api/v1/invite-codes/redeem")
    def redeem_invite(
        payload: InviteRedeemIn,
        session: Session = Depends(get_session),
        x_user_id: str | None = Header(default=None),
    ):
        caller = x_user_id.strip() if x_user_id else ""
        if not caller:
            raise HTTPException(status_code=400, detail="user is required")
        try:
            grant = InviteRedemption(
                session, AuditWriter(session, clock(), None, None), clock()
            ).redeem(payload.code, caller)
        except WorkbenchError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
        return {
            "entitlement_id": grant.id,
            "tier": grant.kind,
            "source": grant.source,
            "expires_at": grant.expires_at.isoformat(),
        }

    AdminApi.register(app, get_session, clock, alerts, grant_writer)
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
