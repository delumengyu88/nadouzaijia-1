from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from nadou.db import Base, Database, create_engine_for
from nadou.main import create_app
from nadou.models import (  # noqa: F401
    Entitlement,
    IndexRecord,
    MemberQuery,
    PlatformAlias,
    ProgramAlias,
    QueryProgram,
    Task,
    User,
)

BEIJING = ZoneInfo("Asia/Shanghai")


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
    return TestClient(app)
