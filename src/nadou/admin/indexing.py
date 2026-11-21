from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from nadou.admin.evidence import EvidenceOverdueScanner
from nadou.admin.notify import Notifier
from nadou.filename_parser import FilenameParser
from nadou.importer import TaskExportImporter
from nadou.models import IndexAlertEpisode, IndexRun, IndexRunLock, Task


class TaskExportUnavailable(RuntimeError):
    pass


class IndexRunBusy(RuntimeError):
    """Another index run holds the lock."""


class TaskExportSource:
    """Pulls the latest task export. The real private-storage reader replaces this later."""

    def fetch(self) -> str:
        raise NotImplementedError


class CsvTaskExportSource(TaskExportSource):
    """CSV text uploaded from the workbench. The hourly file reader stays separate."""

    def __init__(self, content: str) -> None:
        self.content = content

    def fetch(self) -> str:
        if not self.content.strip():
            raise TaskExportUnavailable("import file is empty")
        return self.content


class LocalFileTaskExportSource(TaskExportSource):
    """Reads a local CSV. The path comes from the environment; nothing is hard-coded."""

    def __init__(self, path: str) -> None:
        self.path = path

    def fetch(self) -> str:
        if not self.path:
            raise TaskExportUnavailable("NADOU_TASK_EXPORT_PATH is not set")
        file = Path(self.path)
        if not file.is_file():
            raise TaskExportUnavailable("task export file is not available")
        return file.read_text(encoding="utf-8")


class IndexRunGuard:
    """Postgres advisory lock, with a single-row table on other databases."""

    LOCK_ID = 1
    ADVISORY_KEY = 9142001
    STALE_AFTER = timedelta(hours=6)

    @staticmethod
    def try_acquire(session: Session, now: datetime) -> bool:
        bind = session.get_bind()
        if bind.dialect.name == "postgresql":
            locked = session.execute(
                text("SELECT pg_try_advisory_lock(:key)"),
                {"key": IndexRunGuard.ADVISORY_KEY},
            ).scalar()
            return bool(locked)
        current = session.get(IndexRunLock, IndexRunGuard.LOCK_ID)
        if current is not None:
            acquired = current.acquired_at
            if now - acquired < IndexRunGuard.STALE_AFTER:
                return False
            session.delete(current)
            session.commit()
        session.add(IndexRunLock(id=IndexRunGuard.LOCK_ID, acquired_at=now))
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            return False
        return True

    @staticmethod
    def release(session: Session) -> None:
        bind = session.get_bind()
        if bind.dialect.name == "postgresql":
            session.execute(
                text("SELECT pg_advisory_unlock(:key)"),
                {"key": IndexRunGuard.ADVISORY_KEY},
            )
            return
        row = session.get(IndexRunLock, IndexRunGuard.LOCK_ID)
        if row is None:
            return
        session.delete(row)
        session.commit()


class IndexMonitor:
    """Alert once when no successful run is newer than two days. A success closes the episode."""

    STALE_AFTER = timedelta(days=2)

    def __init__(self, session: Session, notifier: Notifier, now: Callable[[], datetime]) -> None:
        self.session = session
        self.notifier = notifier
        self.now = now

    def last_successful_at(self) -> datetime | None:
        return IndexStatus.last_successful_at(self.session)

    def check_staleness(self) -> bool:
        now = self.now()
        last = self.last_successful_at()
        if last is None:
            anchor = self.session.scalar(select(func.min(IndexRun.started_at)))
            if anchor is None or now - anchor <= IndexMonitor.STALE_AFTER:
                return False
        elif now - last <= IndexMonitor.STALE_AFTER:
            return False
        if self._open() is not None:
            return False
        self.notifier.notify("索引任务停滞", "超过 2 天没有成功的索引运行")
        self.session.add(IndexAlertEpisode(kind="staleness", opened_at=now, closed_at=None))
        self.session.flush()
        return True

    def note_success(self) -> None:
        open_row = self._open()
        if open_row is None:
            return
        open_row.closed_at = self.now()
        self.session.flush()

    def _open(self) -> IndexAlertEpisode | None:
        return self.session.scalar(
            select(IndexAlertEpisode).where(
                IndexAlertEpisode.kind == "staleness",
                IndexAlertEpisode.closed_at.is_(None),
            )
        )


class IndexAlerts:
    """Read-only rows whose platform could not be resolved. Nothing here is editable."""

    LABEL = "平台未识别"

    @staticmethod
    def unrecognized(session: Session) -> list[dict]:
        rows = list(
            session.scalars(
                select(Task)
                .where(
                    Task.is_test.is_(False),
                    Task.status == "done",
                    or_(Task.source_unverified.is_(True), Task.platform_name.is_(None)),
                )
                .order_by(Task.created_at.desc(), Task.task_id)
            ).all()
        )
        listed = []
        for task in rows:
            parsed = FilenameParser.parse(task.raw_filename or "")
            if parsed.network_type is None and parsed.platform is None:
                reason = "文件名无平台字段，解析失败"
            else:
                reason = "无法解析或不在平台对照表中"
            listed.append(
                {
                    "task_id": task.task_id,
                    "filename": task.raw_filename,
                    "reason": reason,
                    "display": IndexAlerts.LABEL,
                }
            )
        return listed


class IndexStatus:
    @staticmethod
    def last_successful_at(session: Session) -> datetime | None:
        return session.scalar(
            select(func.max(IndexRun.finished_at)).where(IndexRun.status == "success")
        )

    @staticmethod
    def payload(session: Session, now: datetime | None = None) -> dict:
        last = IndexStatus.last_successful_at(session)
        runs = list(
            session.scalars(
                select(IndexRun).order_by(IndexRun.started_at.desc(), IndexRun.id.desc()).limit(80)
            ).all()
        )
        failures = 0
        for run in runs:
            if run.status == "success":
                break
            if run.status == "failed":
                failures += 1
        success = next((run for run in runs if run.status == "success"), None)
        stale = False
        if now is not None and last is not None:
            stale = now - last > IndexMonitor.STALE_AFTER
        latest = runs[0] if runs else None
        return {
            "last_successful_index_at": last.isoformat() if last else None,
            "stale": stale,
            "consecutive_failures": failures,
            "last_success_new_rows": None if success is None else success.new_rows,
            "last_run": None if latest is None else IndexStatus._run(latest),
            "runs": [IndexStatus._run(run) for run in runs],
            "unrecognized": IndexAlerts.unrecognized(session),
        }

    @staticmethod
    def _run(run: IndexRun) -> dict:
        return {
            "id": run.id,
            "started_at": run.started_at.isoformat(),
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "new_rows": run.new_rows,
            "status": run.status,
            "error_summary": run.error_summary,
        }


class IndexJob:
    def __init__(
        self,
        session: Session,
        source: TaskExportSource,
        notifier: Notifier,
        now: Callable[[], datetime],
    ) -> None:
        self.session = session
        self.source = source
        self.notifier = notifier
        self.now = now

    def run(self) -> IndexRun:
        if not IndexRunGuard.try_acquire(self.session, self.now()):
            raise IndexRunBusy()
        try:
            return self._execute()
        finally:
            IndexRunGuard.release(self.session)

    def _execute(self) -> IndexRun:
        EvidenceOverdueScanner(self.session, self.notifier, self.now).scan()
        monitor = IndexMonitor(self.session, self.notifier, self.now)
        monitor.check_staleness()
        run = IndexRun(
            started_at=self.now(),
            finished_at=None,
            new_rows=0,
            status="running",
            error_summary=None,
        )
        self.session.add(run)
        self.session.flush()
        try:
            with self.session.begin_nested():
                content = self.source.fetch()
                result = TaskExportImporter(self.session).import_csv(content)
            run.new_rows = result.inserted
            run.status = "success"
            run.finished_at = self.now()
            monitor.note_success()
        except Exception as exc:
            run.new_rows = 0
            run.status = "failed"
            run.error_summary = (str(exc) or exc.__class__.__name__)[:500]
            run.finished_at = self.now()
        self.session.commit()
        return run


class IndexRunCommand:
    @staticmethod
    def run() -> int:
        from nadou.admin.notify import NotifierFactory
        from nadou.config import get_settings
        from nadou.db import Database

        settings = get_settings()
        if not settings.task_export_path.strip():
            print("NADOU_TASK_EXPORT_PATH is not set")
            return 2
        database = Database(settings.database_url)
        session = database.session()
        outcome: tuple[int, str, int, str | None] | None = None
        try:
            job = IndexJob(
                session,
                LocalFileTaskExportSource(settings.task_export_path),
                NotifierFactory.from_settings(settings),
                IndexRunCommand.now,
            )
            try:
                run = job.run()
            except IndexRunBusy:
                print("index run skipped: another run is active")
                return 0
            outcome = (run.id, run.status, run.new_rows, run.error_summary)
        except Exception as exc:
            session.rollback()
            print(f"index run failed: {exc}")
            return 1
        finally:
            session.close()
        assert outcome is not None
        run_id, status, new_rows, error = outcome
        print(f"index run {run_id} status={status} new_rows={new_rows} error={error or ''}")
        return 0 if status == "success" else 1

    @staticmethod
    def now() -> datetime:
        from nadou.text import RecordAge

        return datetime.now(RecordAge.TZ)
