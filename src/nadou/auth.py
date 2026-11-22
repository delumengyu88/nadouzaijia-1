"""Mini program identity: exchange a WeChat code, then use our own opaque session."""

import hashlib
import logging
import secrets
from datetime import datetime, timedelta
from uuid import uuid4

import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from nadou.config import get_settings
from nadou.models import Entitlement, User, UserSession


class _RedactWeChatQuery(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                arg.copy_with(query=None)
                if isinstance(arg, httpx.URL)
                and any(
                    key in arg.params for key in ("access_token", "pay_sig", "secret", "js_code")
                )
                else arg
                for arg in record.args
            )
        return True


logging.getLogger("httpx").addFilter(_RedactWeChatQuery())


class WeChatLogin:
    SESSION_URL = "https://api.weixin.qq.com/sns/jscode2session"

    @staticmethod
    def exchange(code: str) -> str:
        return WeChatLogin.exchange_session(code)[0]

    @staticmethod
    def exchange_session(code: str) -> tuple[str, str]:
        settings = get_settings()
        if not settings.wx_appid or not settings.wx_appsecret:
            raise HTTPException(503, "WeChat login is unavailable")
        try:
            response = httpx.get(
                WeChatLogin.SESSION_URL,
                params={
                    "appid": settings.wx_appid,
                    "secret": settings.wx_appsecret.get_secret_value(),
                    "js_code": code,
                    "grant_type": "authorization_code",
                },
                timeout=10,
                follow_redirects=False,
            )
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError):
            # Never expose upstream URLs, credentials, session_key or error bodies.
            raise HTTPException(503, "WeChat login is unavailable") from None
        if not isinstance(body, dict):
            raise HTTPException(503, "WeChat login is unavailable")
        if body.get("errcode") in (40029, 40163):
            raise HTTPException(401, "invalid WeChat code")
        openid = body.get("openid")
        if (
            body.get("errcode")
            or not isinstance(openid, str)
            or not openid.strip()
            or len(openid) > 64
            or not isinstance(body.get("session_key"), str)
            or not body["session_key"]
        ):
            raise HTTPException(503, "WeChat login is unavailable")
        return openid, body["session_key"]

    @staticmethod
    def user(session: Session, openid: str, now: datetime) -> User:
        query = select(User).where(User.wechat_openid == openid)
        user = session.scalar(query)
        if user is None:
            try:
                # Concurrent logins for one openid must converge on the unique user.
                with session.begin_nested():
                    user = User(id=str(uuid4()), wechat_openid=openid, created_at=now)
                    session.add(user)
                    session.flush()
            except IntegrityError:
                user = session.scalar(query)
                if user is None:
                    raise
        return user


def require_active(user: User, now: datetime) -> None:
    if user.ban_type == "permanent" or (
        user.ban_type == "temporary" and user.ban_until is not None and user.ban_until > now
    ):
        raise HTTPException(403, "account is disabled")


class UserSessions:
    TTL = timedelta(days=7)

    @staticmethod
    def issue(session: Session, user: User, now: datetime) -> str:
        token = secrets.token_urlsafe(32)
        session.add(
            UserSession(
                token_hash=hashlib.sha256(token.encode()).hexdigest(),
                user_id=user.id,
                created_at=now,
                expires_at=now + UserSessions.TTL,
            )
        )
        session.flush()
        return token

    @staticmethod
    def resolve(session: Session, authorization: str | None, now: datetime) -> User:
        scheme, _, token = (authorization or "").partition(" ")
        token = token.strip()
        row = None
        if scheme.lower() == "bearer" and 1 <= len(token) <= 512:
            row = session.get(UserSession, hashlib.sha256(token.encode()).hexdigest())
        user = session.get(User, row.user_id) if row and row.expires_at > now else None
        if user is None:
            raise HTTPException(
                401, "authentication required", headers={"WWW-Authenticate": "Bearer"}
            )
        require_active(user, now)
        return user


def user_profile(session: Session, user: User, now: datetime) -> dict:
    grants = session.scalars(
        select(Entitlement).where(
            Entitlement.user_id == user.id,
            Entitlement.kind.in_(("vip", "regular")),
            Entitlement.expires_at > now,
        )
    ).all()
    ends = {
        kind: max((g.expires_at for g in grants if g.kind == kind), default=None)
        for kind in ("vip", "regular")
    }
    return {
        "id": user.id,
        "tier": "vip" if ends["vip"] else "regular" if ends["regular"] else "free",
        "vip_expires_at": ends["vip"].isoformat() if ends["vip"] else None,
        "regular_expires_at": ends["regular"].isoformat() if ends["regular"] else None,
    }
