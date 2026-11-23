"""One representative frame per record, shaped by the caller's tier on the server.

Free users only ever receive a blurred, pixelated picture: the clear frame is never
encoded into their response. Members and the bands a user unlocked get the clear,
watermarked frame. A locked record, a summary-only record, or a record younger than
30 days gets no picture at all.
"""

from collections.abc import Callable
from datetime import datetime

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from nadou.bands import frame_view
from nadou.entitlements import EntitlementPolicy
from nadou.frames import DerivedFrameSource, FrameImage, FrameUnavailable
from nadou.models import IndexRecord, Task, User
from nadou.search import ProgramSearch
from nadou.text import RecordAge


class FrameForbidden(Exception):
    pass


class FrameMissing(Exception):
    pass


class RecordFrames:
    def __init__(self, session: Session, frames: DerivedFrameSource | None, now: datetime) -> None:
        self.session = session
        self.frames = frames
        self.now = now

    def view_for(self, record_id: str, user_id: str) -> tuple[IndexRecord, str]:
        record = self.session.get(IndexRecord, record_id)
        if record is None:
            raise FrameMissing(record_id)
        task = self.session.get(Task, record.task_id) if record.task_id else None
        if not ProgramSearch.visible(record, task):
            raise FrameMissing(record_id)
        when = ProgramSearch.tier_time(task, record)
        if when is None:
            raise FrameForbidden(record_id)
        search = ProgramSearch(self.session, None, self.now)
        grants = ProgramSearch._grants_for(
            search._live_grants(user_id), record.program_name, record.id
        )
        decision = EntitlementPolicy.decide(RecordAge.days(when, self.now), grants)
        view = frame_view(decision)
        if view is None:
            raise FrameForbidden(record_id)
        return record, view

    def jpeg(self, record_id: str, user_id: str) -> tuple[bytes, str]:
        record, view = self.view_for(record_id, user_id)
        if self.frames is None:
            raise FrameMissing(record_id)
        # The blurred picture starts from the low-resolution derivative, never the card.
        variant = "card" if view == "clear" else "lowres"
        try:
            fetch_record = getattr(self.frames, "fetch_record", None)
            if fetch_record is not None:
                raw = fetch_record(record)
            else:
                raw = self.frames.fetch_representative(variant, record.id, user_id)
        except FrameUnavailable:
            raise FrameMissing(record_id) from None
        if not raw:
            raise FrameMissing(record_id)
        prepared = FrameImage.prepare(raw) if view == "clear" else FrameImage.blurred(raw)
        return prepared.jpeg, view

    @staticmethod
    def register(
        app: FastAPI,
        get_session,
        clock: Callable[[], datetime],
        frames: DerivedFrameSource | None,
        current_user,
    ) -> None:
        @app.get("/api/v1/records/{record_id}/frame")
        def record_frame(
            record_id: str,
            session: Session = Depends(get_session, scope="function"),
            user: User = Depends(current_user),
        ):
            try:
                body, view = RecordFrames(session, frames, clock()).jpeg(record_id, user.id)
            except FrameMissing:
                raise HTTPException(404, "暂无截图") from None
            except FrameForbidden:
                raise HTTPException(403, "解锁后可查看截图") from None
            return Response(
                content=body,
                media_type="image/jpeg",
                headers={"Cache-Control": "private, no-store", "X-Nadou-Frame": view},
            )
