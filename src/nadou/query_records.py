"""Saved mini-program lookups. One row per confirm, one program per name.

The search route is unchanged. The client creates the row when the member
confirms the name list, then writes one summary after each program search
returns. Summaries use the query's submission date and real detection
results only. Rows with ``date_generated`` are skipped. A missing real
detection time is not replaced with today.
"""

import re
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nadou.auth import UserSessions
from nadou.config import get_settings
from nadou.entitlements import EntitlementPolicy
from nadou.models import Entitlement, IndexRecord, MemberQuery, QueryProgram, Task, User
from nadou.normalize import ProgramNormalizer
from nadou.program_titles import ProgramTitleGuard
from nadou.search import ProgramSearch
from nadou.text import RecordAge

_SPACE = re.compile(r"\s+")


class QueryNotFound(Exception):
    pass


class QueryRejected(Exception):
    def __init__(self, detail: str) -> None:
        self.detail = detail


class CreateQueryIn(BaseModel):
    """``names`` is the confirmed list. ``name`` is one title when ``names`` is omitted."""

    model_config = ConfigDict(extra="forbid")

    names: list[str] | None = Field(default=None, max_length=500)
    name: str | None = None


class ResultIn(BaseModel):
    """The server computes the summary. ``failed`` records a search that did not return."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(max_length=200)
    failed: bool = False


class ResultSummary:
    """Badge text stored on the program row. No detection date is included."""

    MISS_LINE = "当前索引未收录该节目名。"
    RECENT_LINE = "有最新记录，可委托取证"
    FAIL_LINE = "查询失败"
    UNVERIFIED_LINE = "数据待核验"
    PENDING_LINE = "查询中"
    MAX_NAMES = 500
    NAME_LIMIT = 200
    HIT_RESOLUTIONS = frozenset({"exact", "alias", "manual"})

    @staticmethod
    def display_name(raw: str) -> str:
        chars: list[str] = []
        for char in raw:
            code = ord(char)
            if code == 0x3000:
                chars.append(" ")
            elif 0xFF01 <= code <= 0xFF5E:
                chars.append(chr(code - 0xFEE0))
            else:
                chars.append(char)
        return _SPACE.sub(" ", "".join(chars)).strip()

    @staticmethod
    def names_from(payload: CreateQueryIn) -> list[str]:
        raw = payload.names if payload.names is not None else [payload.name or ""]
        if len(raw) > ResultSummary.MAX_NAMES:
            raise QueryRejected("too many names")
        kept: list[str] = []
        seen: set[str] = set()
        for item in raw:
            display = ResultSummary.display_name(item)
            if len(display) > ResultSummary.NAME_LIMIT:
                raise QueryRejected("name is too long")
            canonical = ProgramNormalizer.canonical(display)
            if not canonical or canonical in seen:
                continue
            seen.add(canonical)
            kept.append(display)
        if not kept:
            raise QueryRejected("name is required")
        return kept

    @staticmethod
    def assess(session: Session, raw_name: str, now) -> tuple[str, str, str]:
        """Return resolution, detail line, and canonical name.

        Placeholder rows are ignored via ``reportable``. ``record_time`` is
        the only clock. A missing time is never filled with now.
        """
        canonical = ProgramNormalizer.canonical(raw_name)
        if not canonical or not ProgramTitleGuard.is_billable(canonical):
            if ProgramSearch.empty_outcome() == "unverified":
                return "unverified", ResultSummary.UNVERIFIED_LINE, canonical
            return "miss", ResultSummary.MISS_LINE, canonical
        records = list(
            session.scalars(select(IndexRecord).where(IndexRecord.program_name == canonical)).all()
        )
        tasks = ResultSummary._tasks(session, {row.task_id for row in records})
        real: list[tuple[IndexRecord, Task | None]] = []
        any_old = False
        any_recent = False
        platforms: set[tuple[str, str]] = set()
        for record in records:
            task = tasks.get(record.task_id)
            if not ProgramSearch.visible(record, task):
                continue
            if not ProgramSearch.reportable(record):
                continue
            real.append((record, task))
            when = ProgramSearch.record_time(task, record)
            if when is None:
                continue
            age = RecordAge.days(when, now)
            if age >= 30:
                any_old = True
                key = ProgramSearch._platform_key(task)
                if key is not None:
                    platforms.add(key)
            else:
                any_recent = True
        if any_old:
            detail = f"命中 {len(real)} 条 · 涉及 {len(platforms)} 个平台"
            return "exact", detail, canonical
        if any_recent:
            return "exact", ResultSummary.RECENT_LINE, canonical
        if ProgramSearch.empty_outcome() == "unverified":
            return "unverified", ResultSummary.UNVERIFIED_LINE, canonical
        return "miss", ResultSummary.MISS_LINE, canonical

    @staticmethod
    def display_status(session: Session, program: QueryProgram, now) -> tuple[str, str | None]:
        if not get_settings().demo_data and program.resolution not in {"pending", "failed"}:
            resolution, detail, _ = ResultSummary.assess(session, program.uploaded_name, now)
            return resolution, detail
        return program.resolution, program.detail

    @staticmethod
    def _tasks(session: Session, task_ids: set[str]) -> dict[str, Task]:
        if not task_ids:
            return {}
        rows = session.scalars(select(Task).where(Task.task_id.in_(task_ids))).all()
        return {row.task_id: row for row in rows}

    @staticmethod
    def evidence(
        session: Session, user_id: str, canonical: str | None, now
    ) -> tuple[str | None, str | None]:
        """Program name and visible platforms for 委托取证. Generated dates stay out."""
        if not canonical or not ProgramTitleGuard.is_billable(canonical):
            return None, None
        records = list(
            session.scalars(select(IndexRecord).where(IndexRecord.program_name == canonical)).all()
        )
        tasks = ResultSummary._tasks(session, {row.task_id for row in records})
        grants = ResultSummary._live_grants(session, user_id, now)
        kept: list[tuple[IndexRecord, Task | None]] = []
        for record in records:
            task = tasks.get(record.task_id)
            if not ProgramSearch.visible(record, task):
                continue
            if not ProgramSearch.reportable(record):
                continue
            kept.append((record, task))
        if not kept:
            return None, None
        platforms: list[str] = []
        ordered = sorted(kept, key=lambda item: ResultSummary._evidence_order(item[0], item[1]))
        for record, task in ordered:
            when = ProgramSearch.record_time(task, record)
            if when is None:
                continue
            age = RecordAge.days(when, now)
            access = ProgramSearch._grants_for(grants, canonical, record.id)
            decision = EntitlementPolicy.decide(age, access)
            if decision.locked or not decision.reveal_fields:
                continue
            shown = (
                ProgramSearch.UNRECOGNIZED_PLATFORM
                if ProgramSearch._unverified(task)
                else task.platform_name
            )
            if shown and shown != ProgramSearch.UNRECOGNIZED_PLATFORM and shown not in platforms:
                platforms.append(shown)
        return canonical, ("、".join(platforms) if platforms else None)

    @staticmethod
    def _evidence_order(record: IndexRecord, task: Task | None) -> tuple:
        when = ProgramSearch.record_time(task, record)
        return (when is None, when.timestamp() if when is not None else 0, record.id)

    @staticmethod
    def _live_grants(session: Session, user_id: str, now) -> list[Entitlement]:
        rows = session.scalars(select(Entitlement).where(Entitlement.user_id == user_id)).all()
        return [row for row in rows if row.expires_at > now]

    @staticmethod
    def flags(resolution: str) -> tuple[str, str, bool, bool, bool]:
        if resolution == "pending":
            return ResultSummary.PENDING_LINE, "bad", False, False, False
        if resolution == "unverified":
            return "待核验", "muted", False, False, False
        if resolution == "failed":
            return "失败", "bad", False, False, True
        if resolution in ResultSummary.HIT_RESOLUTIONS:
            return "已命中", "ok", True, False, False
        return "未命中", "muted", False, True, False

    @staticmethod
    def item_line(resolution: str, detail: str | None) -> str:
        if resolution == "pending":
            return ResultSummary.PENDING_LINE
        if resolution == "unverified":
            return ResultSummary.UNVERIFIED_LINE
        if resolution == "failed":
            return detail or ResultSummary.FAIL_LINE
        if resolution == "miss":
            return detail or ResultSummary.MISS_LINE
        return detail or "已命中"

    @staticmethod
    def list_line(created_at, resolution: str, detail: str | None) -> str:
        day = created_at.astimezone(RecordAge.TZ).strftime("%Y-%m-%d")
        if resolution == "pending":
            tail = ResultSummary.PENDING_LINE
        elif resolution == "failed":
            tail = ResultSummary.FAIL_LINE
        elif resolution == "miss":
            tail = "未命中"
        else:
            tail = ResultSummary.item_line(resolution, detail).replace("命中 ", "已命中 ", 1)
        return f"{day} · {tail}"


class QueryRecords:
    """Owner-only query history. Registration matches PublicConfig.register."""

    DEFAULT_LIMIT = 20
    MAX_LIMIT = 100

    @staticmethod
    def register(app: FastAPI, get_session, clock, reader) -> None:
        @app.post("/api/v1/queries", status_code=201)
        def create_query(
            payload: CreateQueryIn,
            response: Response,
            authorization: str | None = Header(default=None),
            session: Session = Depends(get_session, scope="function"),
        ):
            now = clock()
            user, row, _ = UserSessions.credentials(session, authorization, now)
            try:
                result = QueryRecords.create(session, user, payload, now)
                if user.is_guest:
                    expires = UserSessions.renew_guest(session, user, row, now)
                    response.headers["X-Nadou-Session-Expires-At"] = expires.astimezone(
                        RecordAge.TZ
                    ).isoformat()
                return result
            except QueryRejected as exc:
                raise HTTPException(status_code=400, detail=exc.detail) from None

        @app.get("/api/v1/queries")
        def list_queries(
            user: User = Depends(reader),
            session: Session = Depends(get_session, scope="function"),
            limit: int = Query(default=QueryRecords.DEFAULT_LIMIT, ge=1, le=QueryRecords.MAX_LIMIT),
            offset: int = Query(default=0, ge=0),
        ):
            return QueryRecords.list_for(session, user, limit, offset, clock())

        @app.get("/api/v1/queries/{query_id}")
        def get_query(
            query_id: str,
            user: User = Depends(reader),
            session: Session = Depends(get_session, scope="function"),
        ):
            try:
                return QueryRecords.get(session, user, query_id, clock())
            except QueryNotFound:
                raise HTTPException(status_code=404, detail="query not found") from None

        @app.post("/api/v1/queries/{query_id}/results")
        def record_result(
            query_id: str,
            payload: ResultIn,
            user: User = Depends(reader),
            session: Session = Depends(get_session, scope="function"),
        ):
            try:
                return QueryRecords.record(session, user, query_id, payload, clock())
            except QueryNotFound:
                raise HTTPException(status_code=404, detail="query not found") from None
            except QueryRejected as exc:
                raise HTTPException(status_code=400, detail=exc.detail) from None

        @app.post("/api/v1/queries/{query_id}/seen")
        def mark_seen(
            query_id: str,
            user: User = Depends(reader),
            session: Session = Depends(get_session, scope="function"),
        ):
            try:
                return QueryRecords.mark_seen(session, user, query_id, clock())
            except QueryNotFound:
                raise HTTPException(status_code=404, detail="query not found") from None

    @staticmethod
    def create(session: Session, user: User, payload: CreateQueryIn, now) -> dict:
        names = ResultSummary.names_from(payload)
        query = MemberQuery(id=str(uuid4()), user_id=user.id, created_at=now)
        session.add(query)
        for position, name in enumerate(names):
            session.add(
                QueryProgram(
                    query_id=query.id,
                    position=position,
                    uploaded_name=name,
                    resolution="pending",
                )
            )
        session.flush()
        return QueryRecords._detail(session, user, query, now)

    @staticmethod
    def list_for(session: Session, user: User, limit: int, offset: int, now) -> dict:
        owned = MemberQuery.user_id == user.id
        total = session.scalar(
            select(func.count())
            .select_from(QueryProgram)
            .join(MemberQuery, QueryProgram.query_id == MemberQuery.id)
            .where(owned)
        )
        rows = session.execute(
            select(QueryProgram, MemberQuery)
            .join(MemberQuery, QueryProgram.query_id == MemberQuery.id)
            .where(owned)
            .order_by(
                MemberQuery.created_at.desc(),
                MemberQuery.id.desc(),
                QueryProgram.position.asc(),
            )
            .offset(offset)
            .limit(limit)
        ).all()
        items = []
        for program, query in rows:
            resolution, detail = ResultSummary.display_status(session, program, now)
            badge, badge_cls, _hit, _missed, _failed = ResultSummary.flags(resolution)
            items.append(
                {
                    "id": query.id,
                    "name": program.uploaded_name,
                    "badge": badge,
                    "badgeCls": badge_cls,
                    "line": ResultSummary.list_line(query.created_at, resolution, detail),
                }
            )
        return {"items": items, "total": int(total or 0), "limit": limit, "offset": offset}

    @staticmethod
    def get(session: Session, user: User, query_id: str, now) -> dict:
        query = QueryRecords._owned(session, user.id, query_id)
        return QueryRecords._detail(session, user, query, now)

    @staticmethod
    def record(session: Session, user: User, query_id: str, payload: ResultIn, now) -> dict:
        query = QueryRecords._owned(session, user.id, query_id)
        display = ResultSummary.display_name(payload.name)
        canonical = ProgramNormalizer.canonical(display)
        if not canonical:
            raise QueryRejected("name is required")
        program = QueryRecords._program(session, query.id, canonical)
        if program is None:
            raise QueryRejected("name is not in this query")
        if payload.failed:
            program.resolution = "failed"
            program.detail = ResultSummary.FAIL_LINE
            program.matched_name = canonical
        else:
            resolution, detail, matched = ResultSummary.assess(session, display, now)
            program.resolution = resolution
            program.detail = detail
            program.matched_name = matched or canonical
        session.flush()
        return QueryRecords._detail(session, user, query, now)

    @staticmethod
    def mark_seen(session: Session, user: User, query_id: str, now) -> dict:
        query = QueryRecords._owned(session, user.id, query_id)
        if query.seen_at is None:
            query.seen_at = now
        session.flush()
        return QueryRecords._detail(session, user, query, now)

    @staticmethod
    def _owned(session: Session, user_id: str, query_id: str) -> MemberQuery:
        query = session.get(MemberQuery, query_id)
        if query is None or query.user_id != user_id:
            raise QueryNotFound(query_id)
        return query

    @staticmethod
    def _program(session: Session, query_id: str, canonical: str) -> QueryProgram | None:
        rows = session.scalars(
            select(QueryProgram)
            .where(QueryProgram.query_id == query_id)
            .order_by(QueryProgram.position)
        ).all()
        for row in rows:
            if ProgramNormalizer.canonical(row.uploaded_name) == canonical:
                return row
            if row.matched_name and ProgramNormalizer.canonical(row.matched_name) == canonical:
                return row
        return None

    @staticmethod
    def _programs(session: Session, query_id: str) -> list[QueryProgram]:
        return list(
            session.scalars(
                select(QueryProgram)
                .where(QueryProgram.query_id == query_id)
                .order_by(QueryProgram.position)
            ).all()
        )

    @staticmethod
    def _detail(session: Session, user: User, query: MemberQuery, now) -> dict:
        programs = QueryRecords._programs(session, query.id)
        items = [QueryRecords._item(session, user, program, now) for program in programs]
        done_count = sum(1 for program in programs if program.resolution != "pending")
        total = len(programs)
        hits = sum(1 for item in items if item["hit"])
        misses = sum(1 for item in items if item["missed"])
        fails = sum(1 for item in items if item["failed"])
        finished = total > 0 and done_count == total
        queued = done_count == 0
        if queued:
            head = ""
        elif not finished:
            head = f"已完成 {done_count} / {total}"
        elif any(item["unverified"] for item in items):
            unverified = sum(1 for item in items if item["unverified"])
            head = f"共 {total} 个｜命中 {hits}｜未命中 {misses}｜待核验 {unverified}｜失败 {fails}"
        elif fails == 0 and hits == 0:
            head = f"共 {total} 条，均未命中。"
        else:
            head = f"共 {total} 个｜命中 {hits}｜未命中 {misses}｜失败 {fails}"
        if fails == 0:
            fail_banner = None
        elif fails == total:
            fail_banner = "全部查询失败，请重试。"
        else:
            fail_banner = "部分查询失败，请重试。"
        return {
            "id": query.id,
            "created_at": query.created_at.isoformat(),
            "seen_at": query.seen_at.isoformat() if query.seen_at else None,
            "queued": queued,
            "running": not queued and not finished,
            "done": finished,
            "head": head,
            "percent": 0 if total == 0 else round(done_count / total * 100),
            "items": items,
            "failCount": fails,
            "showRetryAll": finished and fails > 0,
            "showFailBanner": fails > 0,
            "allFail": finished and fails == total and total > 0,
            "failBanner": fail_banner,
            "itemRetry": fails > 0,
            "networkError": None,
            "showNetworkRetry": None,
        }

    @staticmethod
    def _item(session: Session, user: User, program: QueryProgram, now) -> dict:
        resolution, detail = ResultSummary.display_status(session, program, now)
        badge, badge_cls, hit, missed, failed = ResultSummary.flags(resolution)
        evidence_name = None
        evidence_platform = None
        if not user.is_guest and resolution in ResultSummary.HIT_RESOLUTIONS:
            canonical = ProgramNormalizer.canonical(program.uploaded_name)
            if get_settings().demo_data:
                canonical = program.matched_name or canonical
            evidence_name, evidence_platform = ResultSummary.evidence(
                session, user.id, canonical, now
            )
        return {
            "name": program.uploaded_name,
            "badge": badge,
            "badgeCls": badge_cls,
            "line": ResultSummary.item_line(resolution, detail),
            "hit": hit,
            "missed": missed,
            "unverified": resolution == "unverified",
            "failed": failed,
            "evidenceName": evidence_name,
            "evidencePlatform": evidence_platform,
        }
