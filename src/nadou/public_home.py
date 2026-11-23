"""Public mini-program home. Reads announcements, examples, and the index; never writes."""

import re
from collections.abc import Callable
from datetime import datetime

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from nadou.config import get_settings
from nadou.entitlements import EntitlementPolicy
from nadou.importer import TestTaskFilter
from nadou.models import Announcement, HomepageExample, IndexRecord, PublicExamplePermit, Task
from nadou.normalize import ProgramNormalizer
from nadou.search import ProgramSearch
from nadou.summary_source import digest, row_fingerprint
from nadou.text import RecordAge


class PublicHome:
    """Unauthenticated home content. No user, no entitlement, no default-row insert."""

    EXAMPLE_LIMIT = 5
    CACHE_CONTROL = "no-store"

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
        for row, records in PublicHome.published(session, now):
            summary = PublicHome._summary(records, now)
            cards.append({"name": row.program_name, "line": summary["line"], "sub": summary["sub"]})
        return cards

    @staticmethod
    def example(session: Session, name: str, now: datetime) -> dict | None:
        canonical = ProgramNormalizer.canonical(name)
        chosen_pair = next(
            (
                (row, pairs)
                for row, pairs in PublicHome.published(session, now)
                if ProgramNormalizer.canonical(row.program_name) == canonical
            ),
            None,
        )
        if chosen_pair is None:
            return None
        chosen, rows = chosen_pair
        summary = PublicHome._summary(rows, now)
        # Same rows as line/sub. Under 30 days is the forensic bucket: no grant
        # reveals it, so a public report omits the row instead of listing it.
        visible = [
            (record, task)
            for record, task in rows
            if PublicHome._hit_time(task, record, now) is not None
        ]
        ordered = PublicHome._by_real_date(visible)
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
            "evidence_program_name": chosen.program_name if rows else None,
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
        matching = select(IndexRecord.task_id).where(
            IndexRecord.hit_type == "consensus", IndexRecord.summary_grant.is_(None)
        )
        if not get_settings().demo_data:
            matching = matching.where(
                IndexRecord.task_id == Task.task_id,
                IndexRecord.hitlist_sha256 == Task.approved_hitlist_sha256,
                IndexRecord.date_generated.is_(False),
                (
                    Task.created_at.is_not(None)
                    | Task.file_uploaded_at.is_not(None)
                    | IndexRecord.uploaded_at.is_not(None)
                ),
            ).correlate(Task)
        tasks = session.scalars(select(Task).where(Task.task_id.in_(matching))).all()
        keys = set()
        for task in tasks:
            if not get_settings().demo_data and not TestTaskFilter.commercial_task(task):
                continue
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

    @classmethod
    def published(cls, session: Session, now: datetime) -> list:
        online = cls._online_examples(session)
        groups, tokens = cls.example_records(session, now, [row.program_name for row in online])
        return cls.published_rows(online, groups, tokens)

    @classmethod
    def published_rows(cls, rows, groups, tokens) -> list:
        chosen = []
        seen = set()
        for row in rows:
            name = ProgramNormalizer.canonical(row.program_name)
            pairs = groups.get(name, [])
            if not get_settings().demo_data:
                if (
                    not row.record_id
                    or not row.publication_token
                    or tokens.get(row.record_id) != row.publication_token
                ):
                    continue
                pairs = [(rec, task) for rec, task in pairs if rec.id == row.record_id]
            if row.online and name not in seen and cls.example_eligible(pairs, row.record_id):
                chosen.append((row, pairs))
                seen.add(name)
                if len(chosen) == cls.EXAMPLE_LIMIT:
                    break
        return chosen

    @staticmethod
    def example_eligible(pairs, record_id: str | None) -> bool:
        if record_id:
            return any(record.id == record_id for record, _ in pairs)
        return bool(pairs) or get_settings().demo_data

    @staticmethod
    def public_row_hash(record: IndexRecord, task: Task) -> str:
        # The permit identifies the precise row, source hash, and all public report fields.
        return digest(
            [
                row_fingerprint(record, task),
                record.category,
                task.platform_subtype,
                task.file_uploaded_at.isoformat() if task.file_uploaded_at else None,
                record.uploaded_at.isoformat() if record.uploaded_at else None,
            ]
        )

    @classmethod
    def example_records(cls, session: Session, now: datetime, names=None) -> tuple[dict, dict]:
        """Fetch qualifying rows in one join. Only a targeted or authorized subset enters Python."""
        settings = get_settings()
        demo = settings.demo_data
        stmt = (
            select(IndexRecord, Task)
            .outerjoin(Task, Task.task_id == IndexRecord.task_id)
            .where(
                IndexRecord.hit_type == "consensus",
                IndexRecord.date_generated.is_(False),
                IndexRecord.summary_grant.is_(None),
            )
        )
        if names is not None:
            names = {ProgramNormalizer.canonical(name) for name in names}
            if not names:
                return {}, {}
            stmt = stmt.where(IndexRecord.program_name.in_(names))
        if not demo:
            stmt = (
                stmt.add_columns(PublicExamplePermit)
                .join(PublicExamplePermit, PublicExamplePermit.record_id == IndexRecord.id)
                .where(
                    PublicExamplePermit.active.is_(True),
                    IndexRecord.hitlist_sha256 == PublicExamplePermit.source_sha256,
                    Task.status == "done",
                    Task.is_test.is_(False),
                    IndexRecord.hitlist_sha256 == Task.approved_hitlist_sha256,
                )
            )
        groups = {}
        tokens = {}
        for row in session.execute(stmt.order_by(IndexRecord.program_name, IndexRecord.id)):
            rec, task = row[:2]
            if not ProgramSearch.visible(rec, task) or not ProgramSearch.reportable(rec):
                continue
            if not demo and (
                cls._hit_time(task, rec, now) is None
                or task.source_unverified
                or not re.fullmatch("[0-9a-f]{64}", row[2].permission_sha256)
                or row[2].row_sha256 != cls.public_row_hash(rec, task)
            ):
                continue
            if not demo:
                tokens[rec.id] = row[2].publication_token
            groups.setdefault(rec.program_name, []).append((rec, task))
        return groups, tokens

    @staticmethod
    def _hit_time(task: Task | None, record: IndexRecord, now: datetime) -> datetime | None:
        """Real detection time for a row the public example may list.

        ``EntitlementPolicy.age_bucket`` is the same 30-day cut search uses.
        ``lt_30`` is forensic for every caller, including one with no grants.
        A missing real detection time is not a hit either.
        """
        when = ProgramSearch.record_time(task, record)
        if when is None:
            return None
        if EntitlementPolicy.age_bucket(RecordAge.days(when, now)) == "lt_30":
            return None
        return when

    @staticmethod
    def _summary(rows: list[tuple[IndexRecord, Task | None]], now: datetime) -> dict:
        hits: list[tuple[IndexRecord, Task | None, datetime]] = []
        platforms: set[tuple[str, str]] = set()
        unverified = 0
        any_recent = False
        for record, task in rows:
            when = ProgramSearch.record_time(task, record)
            if when is None:
                continue
            hit = PublicHome._hit_time(task, record, now)
            if hit is None:
                any_recent = True
                continue
            hits.append((record, task, hit))
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
            if PublicHome._hit_time(task, record, now) is None:
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
