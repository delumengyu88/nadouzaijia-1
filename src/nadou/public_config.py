"""Public mini-program config. Reads splash_config and never writes."""

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from nadou.models import SplashConfig


class PublicConfig:
    """Unauthenticated app config. The workbench owns the single splash row."""

    ROW_ID = 1
    CACHE_CONTROL = "public, max-age=60"

    @staticmethod
    def splash(session: Session) -> dict[str, bool | str]:
        # Do not call SplashBook.get(): a missing row must stay missing.
        row = session.get(SplashConfig, PublicConfig.ROW_ID)
        if row is None:
            return {"splash_enabled": True, "slogan": ""}
        return {"splash_enabled": bool(row.enabled), "slogan": row.slogan}

    @staticmethod
    def register(app: FastAPI, get_session) -> None:
        @app.get("/api/v1/app/config")
        def app_config(session: Session = Depends(get_session, scope="function")):
            return JSONResponse(
                PublicConfig.splash(session),
                headers={"Cache-Control": PublicConfig.CACHE_CONTROL},
            )
