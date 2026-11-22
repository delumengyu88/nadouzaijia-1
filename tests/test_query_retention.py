from datetime import timedelta

from sqlalchemy import select

from nadou.db import Database
from nadou.models import Entitlement, MemberQuery, QueryProgram, User
from nadou.query_retention import clean_queries


def test_query_retention_only_removes_expired_owned_history(session, clock):
    owners = ["free", "vip", "regular", "expired", "single"]
    session.add_all([User(id=owner, created_at=clock) for owner in owners])
    session.flush()
    for owner, kind, expiry in (
        ("vip", "vip", 1),
        ("regular", "regular", 1),
        ("expired", "regular", 0),
        ("single", "program_90", 1),
    ):
        session.add(
            Entitlement(user_id=owner, kind=kind, expires_at=clock + timedelta(days=expiry))
        )
    ages = [
        ("old-free", "free", 30),
        ("older-free", "free", 31),
        ("young-free", "free", 29),
        ("old-vip", "vip", 180),
        ("young-vip", "vip", 179),
        ("young-member", "regular", 31),
        ("old-expired-member", "expired", 31),
        ("old-single", "single", 31),
        ("legacy", None, 999),
    ]
    for number, owner, days in ages:
        session.add(MemberQuery(id=number, user_id=owner, created_at=clock - timedelta(days=days)))
        session.flush()
        session.add(
            QueryProgram(query_id=number, position=0, uploaded_name="片名", resolution="miss")
        )
    session.commit()
    database = Database(engine=session.get_bind())
    assert clean_queries(database, clock, apply=False, batch_size=1) == 5
    assert len(session.scalars(select(MemberQuery.id)).all()) == len(ages)
    session.commit()
    assert clean_queries(database, clock, apply=True, batch_size=1) == 5
    expected = {"young-free", "young-vip", "young-member", "legacy"}
    assert set(session.scalars(select(MemberQuery.id))) == expected
    assert set(session.scalars(select(QueryProgram.query_id))) == expected
    assert clean_queries(database, clock, apply=True, batch_size=1) == 0
