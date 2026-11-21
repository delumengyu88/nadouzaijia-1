from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from nadou.config import get_settings
from nadou.db import Database
from nadou.frames import DerivedFrameSource, GeneratedFrameReader, SignedDerivedFrameSource
from nadou.query_export import ExportForbidden, ExportLimits, QueryExport, UnknownQuery
from nadou.schemas import SearchOut
from nadou.search import EmptyQuery, ProgramSearch, UnknownUser
from nadou.signed_url import FakeSignedUrlProvider, SignedUrlProvider

_BEIJING = ZoneInfo("Asia/Shanghai")


def create_app(
    database: Database | None = None,
    urls: SignedUrlProvider | None = None,
    now: Callable[[], datetime] | None = None,
    frames: DerivedFrameSource | None = None,
    export_max_bytes: int | None = None,
) -> FastAPI:
    database = database or Database(get_settings().database_url)
    urls = urls or FakeSignedUrlProvider()
    clock = now or (lambda: datetime.now(_BEIJING))
    frame_source = frames or SignedDerivedFrameSource(urls, GeneratedFrameReader())
    max_bytes = ExportLimits.MAX_BYTES if export_max_bytes is None else export_max_bytes
    app = FastAPI(title="那豆在架")

    def get_session():
        session = database.session()
        try:
            yield session
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

    @app.get("/api/v1/queries/{query_id}/export")
    def export_query(
        query_id: str,
        session: Session = Depends(get_session),
        x_user_id: str | None = Header(default=None),
    ):
        caller = x_user_id.strip() if x_user_id else None
        if not caller:
            caller = None
        try:
            exported = QueryExport(session, frame_source, clock(), max_bytes).export(
                query_id, caller
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

    return app


app = create_app()
