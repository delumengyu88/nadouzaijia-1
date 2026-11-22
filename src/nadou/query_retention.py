"""Expire owned mini-program history in bounded transactions."""

import argparse
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select

from nadou.config import get_settings
from nadou.db import Database
from nadou.models import Entitlement, MemberQuery, QueryProgram, User


def clean_queries(database: Database, now: datetime, *, apply: bool, batch_size: int = 250) -> int:
    total = 0
    last_user = None
    while True:
        users = select(User.id).order_by(User.id).limit(batch_size)
        if last_user is not None:
            users = users.where(User.id > last_user)
        with database.session() as session:
            owners = session.scalars(users).all()
        if not owners:
            return total
        for user_id in owners:
            while True:
                with database.session() as session, session.begin():
                    if apply:
                        session.execute(select(User.id).where(User.id == user_id).with_for_update())
                    live_member = session.scalar(
                        select(Entitlement.id)
                        .where(
                            Entitlement.user_id == user_id,
                            Entitlement.kind.in_(("regular", "vip")),
                            Entitlement.expires_at > now,
                        )
                        .limit(1)
                    )
                    cutoff = now - timedelta(days=180 if live_member is not None else 30)
                    expired = (
                        MemberQuery.user_id == user_id,
                        MemberQuery.created_at <= cutoff,
                    )
                    if not apply:
                        total += session.scalar(
                            select(func.count()).select_from(MemberQuery).where(*expired)
                        )
                        break
                    ids = session.scalars(
                        select(MemberQuery.id)
                        .where(*expired)
                        .order_by(MemberQuery.created_at, MemberQuery.id)
                        .limit(batch_size)
                        .with_for_update()
                    ).all()
                    if ids:
                        session.execute(delete(QueryProgram).where(QueryProgram.query_id.in_(ids)))
                        result = session.execute(
                            delete(MemberQuery).where(*expired, MemberQuery.id.in_(ids))
                        )
                        total += result.rowcount
                if len(ids) < batch_size:
                    break
        last_user = owners[-1]


def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m nadou query-retention")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="only count eligible history (default)")
    mode.add_argument("--apply", action="store_true", help="remove expired owned history")
    parser.add_argument("--daily", action="store_true", help="repeat every 24 hours")
    args = parser.parse_args(argv)
    db = Database(get_settings().database_url)
    try:
        while True:
            count = clean_queries(db, datetime.now(UTC), apply=args.apply)
            print(f"{'removed' if args.apply else 'eligible'} queries: {count}", flush=True)
            if not args.daily:
                return 0
            time.sleep(86400)
    finally:
        db.engine.dispose()
