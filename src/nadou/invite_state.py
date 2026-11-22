"""Mini-program invite binding. Reads invite_codes for the caller and never writes."""

from collections.abc import Callable
from datetime import datetime

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from nadou.auth import UserSessions
from nadou.models import InviteCode, User


class InviteState:
    """Binding survives grant expiry. A redeemed code cannot be used again."""

    CACHE_CONTROL = "no-store"
    UNBOUND_TEXT = "兑换邀请码"

    @staticmethod
    def viewer(session: Session, authorization: str | None, now: datetime) -> User | None:
        # Same session rules as GET /api/v1/auth/me. A missing or invalid token is a guest,
        # not a 401. A banned account still fails closed.
        try:
            return UserSessions.resolve(session, authorization, now)
        except HTTPException as exc:
            if exc.status_code == 401:
                return None
            raise

    @staticmethod
    def mask(code: str) -> str:
        # miniprogram/api/mock.js maskCode: first four characters, then four stars.
        if len(code) < 4:
            return ""
        return code[:4] + "****"

    @staticmethod
    def bind_text(code: str | None) -> str:
        if not code:
            return InviteState.UNBOUND_TEXT
        return "邀请码 " + InviteState.mask(code)

    @staticmethod
    def of(session: Session, user: User | None) -> dict:
        if user is None:
            return {"guest": True, "bound": False, "bindText": InviteState.UNBOUND_TEXT}
        # Redeemed rows stay bound after the regular entitlement expires.
        # Do not consult entitlements, and do not read another user's code.
        code = session.scalar(
            select(InviteCode.code)
            .where(InviteCode.redeemed_by == user.id)
            .order_by(InviteCode.redeemed_at, InviteCode.id)
        )
        if code is None:
            return {"guest": False, "bound": False, "bindText": InviteState.UNBOUND_TEXT}
        return {"guest": False, "bound": True, "bindText": InviteState.bind_text(code)}

    @staticmethod
    def register(app: FastAPI, get_session, clock: Callable[[], datetime]) -> None:
        @app.get("/api/v1/invite-codes/state")
        def invite_state(
            authorization: str | None = Header(default=None),
            session: Session = Depends(get_session, scope="function"),
        ):
            body = InviteState.of(session, InviteState.viewer(session, authorization, clock()))
            return JSONResponse(body, headers={"Cache-Control": InviteState.CACHE_CONTROL})
