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


@pytest.mark.parametrize("same_user", [False, True])
def test_concurrent_invite_redemption_is_atomic(same_user):
    from concurrent.futures import ThreadPoolExecutor
    from datetime import UTC, timedelta
    from threading import Barrier
    from uuid import uuid4

    from sqlalchemy import delete, select

    from nadou.admin.audit import AuditWriter
    from nadou.admin.errors import WorkbenchError
    from nadou.admin.invites import InviteAlphabet, InviteRedemption
    from nadou.models import Entitlement, InviteCode, StaffAccount, User

    engine = create_engine_for(_database_url())
    factory = sessionmaker(bind=engine)
    now = datetime.now(UTC)
    user_ids = [str(uuid4()), str(uuid4())]
    code_ids = [str(uuid4()), str(uuid4())]
    tokens = [InviteAlphabet.new_code(), InviteAlphabet.new_code()]
    staff_id = str(uuid4())
    try:
        with factory.begin() as session:
            session.add(
                StaffAccount(
                    id=staff_id,
                    username=staff_id,
                    password_hash="unused",
                    role="admin",
                    created_at=now,
                )
            )
            session.add_all([User(id=uid, created_at=now) for uid in user_ids])
            session.flush()
            session.add_all(
                [
                    InviteCode(
                        id=cid,
                        code=token,
                        channel="并发测试",
                        created_by=staff_id,
                        created_at=now,
                        expires_at=now + timedelta(days=1),
                    )
                    for cid, token in zip(code_ids, tokens, strict=True)
                ]
            )
        barrier = Barrier(2)

        def attempt(index):
            with factory() as session:
                barrier.wait(timeout=10)
                try:
                    InviteRedemption(session, AuditWriter(session, now, None, None), now).redeem(
                        tokens[index if same_user else 0], user_ids[0 if same_user else index]
                    )
                    session.commit()
                    return "ok"
                except WorkbenchError as exc:
                    session.rollback()
                    return exc.detail

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, [0, 1]))
        assert sorted(results) == sorted(["ok", "bound" if same_user else "used"])
        with factory() as session:
            grants = session.scalars(
                select(Entitlement).where(Entitlement.user_id.in_(user_ids))
            ).all()
            consumed = session.scalars(
                select(InviteCode).where(
                    InviteCode.id.in_(code_ids), InviteCode.redeemed_at.is_not(None)
                )
            ).all()
            assert len(grants) == len(consumed) == 1
    finally:
        with factory.begin() as session:
            # Audit rows are append-only; keep the synthetic concurrency event.
            session.execute(delete(Entitlement).where(Entitlement.user_id.in_(user_ids)))
            session.execute(delete(InviteCode).where(InviteCode.id.in_(code_ids)))
            session.execute(delete(User).where(User.id.in_(user_ids)))
            session.execute(delete(StaffAccount).where(StaffAccount.id == staff_id))
        engine.dispose()


def test_review_constraints_and_defaults_on_postgres(pg_session):
    from sqlalchemy import select

    from nadou.models import ReviewProgramTitle

    assert set(pg_session.scalars(select(ReviewProgramTitle.program_name))) >= {
        "家庭",
        "远方",
        "狗",
    }
    record = IndexRecordStore(pg_session).upsert(
        task_id="pg-review",
        program_name="　家庭 ",
        program_name_raw="　家庭 ",
        hit_type="consensus",
        frame_count=2,
        timestamps=["00:00:01"],
    )
    pg_session.flush()
    assert record.hit_type == "review"
    pg_session.expire_all()
    assert pg_session.get(IndexRecord, record.id).hit_type == "review"


def test_review_migration_backfills_existing_rows(monkeypatch):
    """Exercise 0006 -> 0007 in an isolated schema, leaving the suite database intact."""
    import uuid

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text
    from sqlalchemy.engine import make_url

    from nadou.config import get_settings

    original_url = _database_url()
    schema = "trial_review_" + uuid.uuid4().hex
    engine = create_engine_for(original_url)
    url = make_url(original_url).update_query_dict({"options": f"-csearch_path={schema}"})
    isolated = create_engine_for(url.render_as_string(hide_password=False))
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    monkeypatch.setenv("DATABASE_URL", url.render_as_string(hide_password=False))
    get_settings.cache_clear()
    try:
        command.upgrade(Config("alembic.ini"), "20261122_0006")
        with isolated.begin() as connection:
            for name, status in [
                ("家庭", "consensus"),
                ("远方", "consensus"),
                ("狗", "consensus"),
                ("父亲", "consensus"),
                ("家庭", "dispute"),
            ]:
                connection.execute(
                    text(
                        "INSERT INTO index_records "
                        "(id, task_id, program_name, program_name_raw, "
                        "hit_type, frame_count, timestamps) "
                        "VALUES (:id, :id, :name, :name, :status, 1, '[]')"
                    ),
                    {"id": uuid.uuid4().hex, "name": name, "status": status},
                )
        command.upgrade(Config("alembic.ini"), "head")
        with isolated.connect() as connection:
            states = set(
                connection.execute(text("SELECT program_name, hit_type FROM index_records"))
            )
            assert states == {
                ("家庭", "review"),
                ("远方", "review"),
                ("狗", "review"),
                ("父亲", "consensus"),
                ("家庭", "dispute"),
            }
            assert connection.scalar(text("SELECT count(*) FROM review_program_titles")) == 3
        command.downgrade(Config("alembic.ini"), "20261122_0006")
        with isolated.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM index_records WHERE hit_type = 'review'")
                )
                == 0
            )
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM index_records WHERE hit_type = 'consensus'")
                )
                == 1
            )
        command.upgrade(Config("alembic.ini"), "head")
    finally:
        isolated.dispose()
        monkeypatch.setenv("DATABASE_URL", original_url)
        get_settings.cache_clear()
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()


def test_user_session_round_trip_and_expiry(pg_session):
    from datetime import timedelta

    from fastapi import HTTPException
    from sqlalchemy import select

    from nadou.auth import UserSessions
    from nadou.models import User, UserSession

    now = datetime(2026, 11, 22, 12, 0, tzinfo=RecordAge.TZ)
    user = User(id="pg-login-user", created_at=now)
    pg_session.add(user)
    pg_session.flush()
    token = UserSessions.issue(pg_session, user, now)
    pg_session.expire_all()
    stored = pg_session.scalar(select(UserSession).where(UserSession.user_id == user.id))
    assert stored.expires_at == now + timedelta(days=7)
    assert stored.token_hash != token
    assert UserSessions.resolve(pg_session, "Bearer " + token, now).id == user.id
    with pytest.raises(HTTPException) as error:
        UserSessions.resolve(pg_session, "Bearer " + token, stored.expires_at)
    assert error.value.status_code == 401


def test_overlapping_renewal_callbacks_stack_once_per_order(pg_session, clock):
    from concurrent.futures import ThreadPoolExecutor
    from datetime import timedelta
    from threading import Barrier
    from types import SimpleNamespace
    from uuid import uuid4

    from sqlalchemy import select

    from nadou.admin.notify import FakeNotifier
    from nadou.admin.orders import OrderGrantWriter
    from nadou.models import Entitlement, Order, OrderIssuance, User
    from nadou.payments.service import PaymentService

    owner = uuid4().hex
    pg_session.add(User(id=owner, created_at=clock, wechat_openid=owner))
    pg_session.flush()
    numbers = [uuid4().hex, uuid4().hex]
    for number in numbers:
        pg_session.add(
            Order(
                id=number,
                order_number=number,
                user_id=owner,
                tier="vip",
                duration_days=30,
                amount=599,
                amount_fen=59900,
                status="待支付",
                created_at=clock,
            )
        )
    pg_session.commit()
    service = PaymentService(lambda: clock, FakeNotifier(), OrderGrantWriter())
    config = SimpleNamespace(appid="test-app", mchid="test-merchant")
    workers_ready = Barrier(3)
    factory = sessionmaker(pg_session.get_bind())

    def confirm(number):
        with factory.begin() as worker:
            order = worker.get(Order, number)
            workers_ready.wait(timeout=10)
            txn = {
                "appid": config.appid,
                "mchid": config.mchid,
                "out_trade_no": number,
                "trade_state": "SUCCESS",
                "amount": {"total": 59900},
                "payer": {"openid": owner},
                "transaction_id": number,
            }
            service._confirm(worker, order, config, txn, notify=True)

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(confirm, n) for n in [*numbers, numbers[0]]]
        for future in futures:
            future.result(timeout=20)
    ends = sorted(
        pg_session.scalars(select(Entitlement.expires_at).where(Entitlement.user_id == owner))
    )
    assert ends == [clock + timedelta(days=30), clock + timedelta(days=60)]
    assert (
        len(
            pg_session.scalars(
                select(OrderIssuance).where(OrderIssuance.order_id.in_(numbers))
            ).all()
        )
        == 2
    )
