"""Public mini-program home. Reads announcements, examples, and the index; never writes."""

from collections.abc import Callable
from datetime import datetime

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from nadou.entitlements import EntitlementPolicy
from nadou.models import Announcement, HomepageExample, IndexRecord, Task
from nadou.normalize import ProgramNormalizer
from nadou.search import ProgramSearch
from nadou.text import RecordAge


class PublicHome:
    """Unauthenticated home content. No user, no entitlement, no default-row insert."""

    CACHE_CONTROL = "public, max-age=60"

    @staticmethod
    def register(app: FastAPI, get_session, now: Callable[[], datetime]) -> None:
        @app.get("/api/v1/app/home")
        def app_home(session: Session = Depends(get_session, scope="function")):
            return JSONResponse(
                PublicHome.home(session, now()),
                headers={"Cache-Control": PublicHome.CACHE_CONTROL},
            )

        @app.get("/api/v1/app/examples/{name}")
        def app_example(name: str, session: Session = Depends(get_session, scope="function")):
            body = PublicHome.example(session, name, now())
            status = 200 if body is not None else 404
            return JSONResponse(
                body if body is not None else {"detail": "example not found"},
                status_code=status,
                headers={"Cache-Control": PublicHome.CACHE_CONTROL},
            )

    @staticmethod
    def home(session: Session, now: datetime) -> dict:
        return {
            "announcements": PublicHome.announcements(session, now),
            "examples": PublicHome.examples(session, now),
            "platformTotal": PublicHome.platform_total(session),
        }

    @staticmethod
    def announcements(session: Session, now: datetime) -> list[dict]:
        # Same order as AnnouncementBook.list_rows. Do not call that book:
        # the public read must not depend on a writer, and it only returns
        # rows the workbench would still call online and in window.
        rows = session.scalars(
            select(Announcement).order_by(Announcement.starts_at.desc(), Announcement.id)
        ).all()
        visible = [row for row in rows if row.online and row.starts_at <= now and row.ends_at > now]
        return [PublicHome._announcement(row) for row in visible]

    @staticmethod
    def examples(session: Session, now: datetime) -> list[dict]:
        cards = []
        for row in PublicHome._online_examples(session):
            summary = PublicHome._summary(PublicHome._reportable(session, row.program_name), now)
            cards.append({"name": row.program_name, "line": summary["line"], "sub": summary["sub"]})
        return cards

    @staticmethod
    def example(session: Session, name: str, now: datetime) -> dict | None:
        chosen = PublicHome._match_example(session, name)
        if chosen is None:
            return None
        rows = PublicHome._reportable(session, chosen.program_name)
        summary = PublicHome._summary(rows, now)
        ordered = PublicHome._by_real_date(rows)
        return {
            "name": chosen.program_name,
            "program_name": ProgramNormalizer.canonical(chosen.program_name),
            "example": True,
            "outcome": summary["outcome"],
            "platform_count": summary["platform_count"],
            "unverified_source_count": summary["unverified_source_count"],
            "line": summary["line"],
            "sub": summary["sub"],
            "evidence_platforms": PublicHome._evidence_platforms(ordered, now),
            "evidence_program_name": chosen.program_name if ordered else None,
            "records": [PublicHome._record(record, task, now) for record, task in ordered],
        }

    @staticmethod
    def platform_total(session: Session) -> int:
        """Distinct countable platforms on consensus index rows.

        A platform is ProgramSearch._platform_key: (province or "", operator).
        Subtype and 公网/专网 do not split it. The task must be included
        (done, not a test) and not ProgramSearch._unverified (no operator,
        unverified source, blank name, 其他平台, or 平台未识别). Age and
        date_generated are not part of this census; those filters apply only
        when a date is shown on an example.
        """
        tasks = session.scalars(
            select(Task).where(
                Task.task_id.in_(
                    select(IndexRecord.task_id).where(IndexRecord.hit_type == "consensus")
                )
            )
        ).all()
        keys = set()
        for task in tasks:
            key = PublicHome.countable_platform(task)
            if key is not None:
                keys.add(key)
        return len(keys)

    @staticmethod
    def countable_platform(task: Task | None) -> tuple[str, str] | None:
        if not ProgramSearch.included(task) or ProgramSearch._unverified(task):
            return None
        return ProgramSearch._platform_key(task)

    @staticmethod
    def _announcement(row: Announcement) -> dict:
        return {
            "id": row.id,
            "title": row.title,
            "body": row.body,
            "button_text": row.button_text,
            "jump_label": row.jump_label,
            "frequency": row.frequency,
            "starts_at": row.starts_at.isoformat(),
            "ends_at": row.ends_at.isoformat(),
        }

    @staticmethod
    def _online_examples(session: Session) -> list[HomepageExample]:
        return list(
            session.scalars(
                select(HomepageExample)
                .where(HomepageExample.online.is_(True))
                .order_by(HomepageExample.sort_order, HomepageExample.id)
            ).all()
        )

    @staticmethod
    def _match_example(session: Session, name: str) -> HomepageExample | None:
        canonical = ProgramNormalizer.canonical(name)
        if not canonical:
            return None
        for row in PublicHome._online_examples(session):
            if ProgramNormalizer.canonical(row.program_name) == canonical:
                return row
        return None

    @staticmethod
    def _reportable(session: Session, program_name: str) -> list[tuple[IndexRecord, Task | None]]:
        """Consensus rows search would keep, minus any date_generated row.

        Generated placeholders are excluded the same way exports and formal
        reports exclude them. Their generated_uploaded_at is never a checked_at.
        """
        canonical = ProgramNormalizer.canonical(program_name)
        if not canonical:
            return []
        records = list(
            session.scalars(select(IndexRecord).where(IndexRecord.program_name == canonical)).all()
        )
        task_ids = {record.task_id for record in records}
        tasks: dict[str, Task] = {}
        if task_ids:
            for task in session.scalars(select(Task).where(Task.task_id.in_(task_ids))).all():
                tasks[task.task_id] = task
        kept: list[tuple[IndexRecord, Task | None]] = []
        for record in records:
            task = tasks.get(record.task_id)
            if not ProgramSearch.confirmed(record) or not ProgramSearch.included(task):
                continue
            if not ProgramSearch.reportable(record):
                continue
            kept.append((record, task))
        return kept

    @staticmethod
    def _age(task: Task | None, record: IndexRecord, now: datetime) -> int | None:
        when = ProgramSearch.record_time(task, record)
        if when is None:
            return None
        return RecordAge.days(when, now)

    @staticmethod
    def _summary(rows: list[tuple[IndexRecord, Task | None]], now: datetime) -> dict:
        hits: list[tuple[IndexRecord, Task | None, datetime]] = []
        platforms: set[tuple[str, str]] = set()
        unverified = 0
        any_recent = False
        for record, task in rows:
            when = ProgramSearch.record_time(task, record)
            age = RecordAge.days(when, now) if when is not None else None
            if age is None or when is None:
                continue
            if age < 30:
                any_recent = True
                continue
            hits.append((record, task, when))
            key = PublicHome.countable_platform(task)
            if key is None:
                unverified += 1
            else:
                platforms.add(key)
        latest = max((when for _record, _task, when in hits), default=None)
        sub = f"命中 {len(hits)} 条记录"
        if latest is not None:
            sub = f"{sub} · {latest.astimezone(RecordAge.TZ).date().isoformat()}"
        if hits:
            outcome = "hit"
        elif any_recent:
            outcome = "recent_only"
        else:
            outcome = "miss"
        return {
            "line": f"涉及 {len(platforms)} 个平台",
            "sub": sub,
            "platform_count": len(platforms),
            "unverified_source_count": unverified,
            "outcome": outcome,
        }

    @staticmethod
    def _by_real_date(
        rows: list[tuple[IndexRecord, Task | None]],
    ) -> list[tuple[IndexRecord, Task | None]]:
        def key(item: tuple[IndexRecord, Task | None]) -> tuple[bool, float, str]:
            when = ProgramSearch.record_time(item[1], item[0])
            return (when is None, -(when.timestamp()) if when is not None else 0.0, item[0].id)

        return sorted(rows, key=key)

    @staticmethod
    def _evidence_platforms(
        rows: list[tuple[IndexRecord, Task | None]], now: datetime
    ) -> list[str]:
        # Recognized platforms on rows old enough to count as hits.
        # date_generated rows are already gone. Under-30 rows stay out, matching
        # search, which only lists platforms from open (not forensic) records.
        names: list[str] = []
        for record, task in rows:
            if PublicHome.countable_platform(task) is None or task is None:
                continue
            age = PublicHome._age(task, record, now)
            if age is None or age < 30:
                continue
            name = task.platform_name
            if name and name not in names:
                names.append(name)
        return names

    @staticmethod
    def _record(record: IndexRecord, task: Task | None, now: datetime) -> dict:
        when = ProgramSearch.record_time(task, record)
        age = RecordAge.days(when, now) if when is not None else None
        unverified = ProgramSearch._unverified(task)
        platform = ProgramSearch.UNRECOGNIZED_PLATFORM if unverified else task.platform_name
        return {
            "record_id": record.id,
            "platform": platform,
            "platform_province": None if unverified or task is None else task.platform_province,
            "platform_operator": None if unverified or task is None else task.platform_operator,
            "platform_subtype": None if unverified or task is None else task.platform_subtype,
            "network_type": None if task is None else task.network_type,
            "category": record.category,
            "frame_count": record.frame_count,
            "timestamps": list(record.timestamps or []),
            "checked_at": when.astimezone(RecordAge.TZ).isoformat() if when is not None else None,
            "age_bucket": None if age is None else EntitlementPolicy.age_bucket(age),
            # No caller and no signer: these search fields have no public source.
            "thumbnail_url": None,
            "hd_url": None,
            "access": None,
            "can_download_report": None,
            "can_download_screenshot": None,
            "locked": None,
        }
