from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from nadou.admin.auth import StaffDirectory
from nadou.admin.notify import FakeNotifier
from nadou.db import Base, Database, create_engine_for
from nadou.main import create_app
from nadou.models import (  # noqa: F401
    Entitlement,
    IndexRecord,
    MemberQuery,
    QueryProgram,
    Task,
    User,
)
from tests.user_helpers import UserClient

BEIJING = ZoneInfo("Asia/Shanghai")


@pytest.fixture(autouse=True)
def synthetic_catalog(monkeypatch):
    # Tests below build synthetic records; the normal application defaults to verified sources.
    from nadou.config import get_settings

    monkeypatch.setenv("NADOU_DATA_SOURCE", "demo")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def clock() -> datetime:
    return datetime(2026, 11, 21, 12, 0, tzinfo=BEIJING)


@pytest.fixture
def session(tmp_path):
    engine = create_engine_for(f"sqlite:///{tmp_path}/nadou.db")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    db_session = factory()
    try:
        yield db_session
    finally:
        db_session.close()
        engine.dispose()


@pytest.fixture
def client(session, clock):
    app = create_app(database=Database(engine=session.get_bind()), now=lambda: clock)
    return UserClient(app, session, clock, default_user=True)


class MutableClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def workbench(session, clock):
    holder = MutableClock(clock)
    notifier = FakeNotifier()
    app = create_app(
        database=Database(engine=session.get_bind()),
        now=holder,
        notifier=notifier,
    )
    StaffDirectory.create(session, "wb-admin", "admin-pass-1", "admin", clock)
    StaffDirectory.create(session, "wb-support", "support-pass-1", "support", clock)
    session.commit()
    with UserClient(app, session, clock) as client:
        admin_token = _login(client, "wb-admin", "admin-pass-1")
        support_token = _login(client, "wb-support", "support-pass-1")
        yield SimpleNamespace(
            app=app,
            client=client,
            clock=holder,
            notifier=notifier,
            session=session,
            admin_token=admin_token,
            support_token=support_token,
        )


def _login(client: TestClient, username: str, password: str) -> str:
    response = client.post(
        "/api/v1/admin/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200, response.text
    return response.json()["token"]
