"""Only request-body I/O runs on the event loop; payment handlers are sync workers."""

from fastapi import HTTPException, Request
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session


async def notify_body(request: Request) -> bytes:
    body = bytearray()
    async for part in request.stream():
        if len(body) + len(part) > 65536:
            raise HTTPException(413, "通知内容过大")
        body.extend(part)
    return bytes(body)


def bounded_row_lock(session: Session, statement) -> None:
    """Take a Postgres row lock that waits at most 5 s (transaction-local setting)."""
    session.execute(text("SET LOCAL lock_timeout = '5s'"))
    try:
        session.execute(statement)
    except OperationalError as exc:
        if getattr(exc.orig, "sqlstate", None) == "55P03":
            raise HTTPException(503, "处理中，请稍后重试") from None
        raise
