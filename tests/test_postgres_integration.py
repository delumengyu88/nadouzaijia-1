import os
from datetime import datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from nadou.db import create_engine_for
from nadou.importer import TaskExportImporter
from nadou.models import IndexRecord, MemberQuery, QueryProgram, Task
from nadou.records import IndexRecordStore
from nadou.search import ProgramSearch
from nadou.signed_url import FakeSignedUrlProvider
from nadou.text import RecordAge

pytestmark = pytest.mark.postgres

HEADER = "task_id,状态,提交时间,filename,公网/专网,平台名"


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    if not url.startswith("postgresql"):
        if os.environ.get("NADOU_REQUIRE_POSTGRES") == "1":
            pytest.fail("DATABASE_URL must point at Postgres")
        pytest.skip("DATABASE_URL is not set to Postgres")
    return url


@pytest.fixture
def pg_session():
    engine = create_engine_for(_database_url())
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        engine.dispose()


def test_timestamptz_round_trip(pg_session):
    when = datetime(2026, 5, 18, 11, 12, 24, tzinfo=RecordAge.TZ)
    pg_session.add(
        Task(
            task_id="pg-time",
            status="done",
            created_at=when,
            raw_filename="pg-time.mp4",
            network_type="公网",
            platform_name="示例视频",
            source_unverified=False,
            is_test=False,
        )
    )
    pg_session.flush()
    pg_session.expire_all()
    loaded = pg_session.get(Task, "pg-time")
    assert loaded.created_at.tzinfo is not None
    local = loaded.created_at.astimezone(RecordAge.TZ)
    assert (local.year, local.month, local.day, local.hour, local.minute, local.second) == (
        2026,
        5,
        18,
        11,
        12,
        24,
    )


def test_check_constraints_reject_bad_values(pg_session):
    pg_session.add(
        Task(
            task_id="pg-bad-status",
            status="nope",
            raw_filename="x.mp4",
            source_unverified=False,
            is_test=False,
        )
    )
    with pytest.raises(IntegrityError):
        pg_session.flush()
    pg_session.rollback()
    pg_session.expunge_all()
    pg_session.add(
        Task(
            task_id="pg-bad-network",
            status="done",
            raw_filename="x.mp4",
            network_type="其他",
            source_unverified=False,
            is_test=False,
        )
    )
    with pytest.raises(IntegrityError):
        pg_session.flush()
    pg_session.rollback()
    pg_session.expunge_all()
    pg_session.add(
        IndexRecord(
            id="pg-bad-hit",
            task_id="pg-missing",
            program_name="节目",
            program_name_raw="节目",
            hit_type="maybe",
            frame_count=1,
            timestamps=["00:00:01"],
        )
    )
    with pytest.raises(IntegrityError):
        pg_session.flush()


def test_query_program_resolution_constraint(pg_session):
    pg_session.add(
        MemberQuery(
            id="q-bad",
            created_at=datetime(2026, 11, 21, 12, 0, tzinfo=RecordAge.TZ),
        )
    )
    pg_session.flush()
    pg_session.add(
        QueryProgram(
            query_id="q-bad",
            position=0,
            uploaded_name="节目",
            resolution="nope",
        )
    )
    with pytest.raises(IntegrityError):
        pg_session.flush()


def test_importer_and_search_on_postgres(pg_session):
    body = (
        f"{HEADER}\n"
        "pgabc123,done,2026-05-18 11:12:24,公网-示例视频-电影-2026.05.18.mp4,公网,示例视频\n"
    )
    result = TaskExportImporter(pg_session).import_csv(body)
    assert result.inserted == 1
    task = pg_session.get(Task, "pgabc123")
    assert task.platform_name == "示例视频"
    assert task.created_at.astimezone(RecordAge.TZ).hour == 11
    IndexRecordStore(pg_session).upsert(
        task_id="pgabc123",
        program_name="ｐｇ节目",
        program_name_raw="ｐｇ节目",
        hit_type="consensus",
        frame_count=2,
        timestamps=["00:01:02"],
    )
    pg_session.flush()
    found = ProgramSearch(
        pg_session,
        FakeSignedUrlProvider(now=lambda: 1_700_000_000),
        datetime(2026, 11, 21, 12, 0, tzinfo=RecordAge.TZ),
    ).search("ｐｇ节目", None)
    assert found.program_name == "pg节目"
    assert found.outcome == "hit"
    assert found.platform_count == 1
    visible = found.platform_groups[0].records[0]
    assert visible.locked is False
    assert visible.platform == "示例视频"
    assert visible.thumbnail_url.startswith("signed://derived/lowres/")


def test_audit_and_notes_reject_mutation(pg_session):
    from sqlalchemy import text

    from nadou.models import AuditLog

    pg_session.add(
        AuditLog(
            created_at=datetime.now(RecordAge.TZ),
            object_type="user",
            object_id="pg-user",
            action="ban_user",
            before_json=None,
            after_json={"ban_type": "permanent"},
        )
    )
    pg_session.flush()
    with pytest.raises(Exception):
        pg_session.execute(text("UPDATE audit_logs SET action = 'tamper'"))
    pg_session.rollback()
    pg_session.add(
        AuditLog(
            created_at=datetime.now(RecordAge.TZ),
            object_type="user",
            object_id="pg-user",
            action="ban_user",
        )
    )
    pg_session.flush()
    with pytest.raises(Exception):
        pg_session.execute(text("DELETE FROM audit_logs"))
    pg_session.rollback()


def test_autogenerate_has_no_drift():
    from alembic.autogenerate import produce_migrations
    from alembic.runtime.migration import MigrationContext

    from nadou.db import Base

    engine = create_engine_for(_database_url())
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            migration = produce_migrations(context, Base.metadata)
        assert migration.upgrade_ops.is_empty()
    finally:
        engine.dispose()
