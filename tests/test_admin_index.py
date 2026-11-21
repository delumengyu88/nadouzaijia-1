import os
import subprocess
import sys
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from nadou.admin.indexing import (
    IndexJob,
    IndexMonitor,
    IndexRunBusy,
    IndexRunCommand,
    IndexRunGuard,
    LocalFileTaskExportSource,
    TaskExportSource,
)
from nadou.admin.notify import FakeNotifier
from nadou.config import get_settings
from nadou.db import Base, create_engine_for
from nadou.models import EvidenceRequest, IndexRun, Task, User

HEADER = "task_id,状态,提交时间,filename,公网/专网,平台名\n"
CSV = HEADER + "idx00001,done,2026-11-01 11:12:24,公网-示例视频-电影-2026.11.01.mp4,公网,示例视频\n"


class MemorySource(TaskExportSource):
    def __init__(self, body: str, *, fail: bool = False) -> None:
        self.body = body
        self.fail = fail
        self.calls = 0

    def fetch(self) -> str:
        self.calls += 1
        if self.fail:
            raise RuntimeError("export unavailable")
        return self.body


def test_rerun_adds_nothing(session, clock):
    source = MemorySource(CSV)
    notifier = FakeNotifier()
    job = IndexJob(session, source, notifier, lambda: clock)
    first = job.run()
    second = job.run()
    assert first.status == "success"
    assert first.new_rows == 1
    assert second.status == "success"
    assert second.new_rows == 0
    assert session.scalar(select(func.count()).select_from(Task)) == 1
    assert source.calls == 2


def test_failed_run_is_logged(session, clock):
    source = MemorySource(CSV, fail=True)
    run = IndexJob(session, source, FakeNotifier(), lambda: clock).run()
    assert run.status == "failed"
    assert run.finished_at == clock
    assert run.new_rows == 0
    assert "export unavailable" in run.error_summary
    assert session.scalar(select(func.count()).select_from(Task)) == 0


def test_lock_prevents_overlap(tmp_path, clock):
    engine = create_engine_for(f"sqlite:///{tmp_path}/index.db")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    first = factory()
    second = factory()
    try:
        assert IndexRunGuard.try_acquire(first, clock) is True
        source = MemorySource(CSV)
        try:
            IndexJob(second, source, FakeNotifier(), lambda: clock).run()
            raise AssertionError("second run should not start")
        except IndexRunBusy:
            pass
        assert source.calls == 0
        second.rollback()
        IndexRunGuard.release(first)
        run = IndexJob(second, source, FakeNotifier(), lambda: clock).run()
        assert run.status == "success"
        assert source.calls == 1
    finally:
        first.close()
        second.close()
        engine.dispose()


def test_staleness_alerts_once_and_resets(session, clock):
    old = clock - timedelta(days=3)
    session.add(IndexRun(started_at=old, finished_at=old, new_rows=1, status="success"))
    session.flush()
    fake = FakeNotifier()
    current = {"now": clock}
    monitor = IndexMonitor(session, fake, lambda: current["now"])
    assert monitor.check_staleness() is True
    assert monitor.check_staleness() is False
    assert len(fake.messages) == 1
    assert fake.messages[0]["title"] == "索引任务停滞"
    session.add(IndexRun(started_at=clock, finished_at=clock, new_rows=0, status="success"))
    session.flush()
    monitor.note_success()
    assert monitor.check_staleness() is False
    current["now"] = clock + timedelta(days=2, seconds=1)
    assert monitor.check_staleness() is True
    assert monitor.check_staleness() is False
    assert len(fake.messages) == 2


def test_exactly_two_days_is_not_stale(session, clock):
    last = clock - timedelta(days=2)
    session.add(IndexRun(started_at=last, finished_at=last, new_rows=1, status="success"))
    session.flush()
    monitor = IndexMonitor(session, FakeNotifier(), lambda: clock)
    assert monitor.check_staleness() is False


def test_overdue_evidence_is_notified_once_from_the_index_job(session, clock):
    user = User(id="idx-user", created_at=clock, phone="90000004001")
    session.add(user)
    session.add(
        EvidenceRequest(
            id="idx-req",
            application_number="QZ-IDX-1",
            user_id=user.id,
            request_type="单条",
            status="待受理",
            created_at=clock - timedelta(days=4),
        )
    )
    session.flush()
    fake = FakeNotifier()
    job = IndexJob(session, MemorySource(HEADER), fake, lambda: clock)
    job.run()
    job.run()
    overdue = [item for item in fake.messages if item["title"] == "取证申请逾期"]
    assert len(overdue) == 1
    assert "QZ-IDX-1" in overdue[0]["body"]


def test_local_file_source_reads_a_csv(tmp_path):
    path = tmp_path / "export.csv"
    path.write_text(CSV, encoding="utf-8")
    assert LocalFileTaskExportSource(str(path)).fetch() == CSV
    try:
        LocalFileTaskExportSource("").fetch()
        raise AssertionError("empty path should fail")
    except Exception as exc:
        assert "NADOU_TASK_EXPORT_PATH" in str(exc)


def test_cli_requires_an_export_path(monkeypatch):
    monkeypatch.setenv("NADOU_TASK_EXPORT_PATH", "")
    get_settings.cache_clear()
    try:
        assert IndexRunCommand.run() == 2
    finally:
        get_settings.cache_clear()


def test_module_entrypoint():
    env = os.environ.copy()
    env["NADOU_TASK_EXPORT_PATH"] = ""
    result = subprocess.run(
        [sys.executable, "-m", "nadou", "index-run"],
        cwd=os.path.abspath(os.path.join(os.path.dirname(__file__), "..")),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "NADOU_TASK_EXPORT_PATH" in result.stdout + result.stderr
