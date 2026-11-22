"""Generate reversible orphan placeholder dates. Never run during deployment.

Requires an explicit DATABASE_URL and an already-migrated database. No real
uploaded_at value is written. Run --dry-run first; take a database backup before
applying. --undo clears generated values (including rows whose tasks reappear).
"""

import argparse
import hashlib
import os
from datetime import date, datetime, time, timedelta

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from nadou.db import Database
from nadou.models import IndexRecord, Task
from nadou.text import RecordAge

BASE_DATE = date(2026, 11, 22)
SEED = "nadou-orphan-dates-v1"


def generated_time(record_id: str) -> datetime:
    # Independent of DB iteration order, Python's hash seed, or execution date.
    digest = hashlib.sha256(f"{SEED}:{record_id}".encode()).digest()
    days = 60 + int.from_bytes(digest, "big") % 131
    return datetime.combine(BASE_DATE - timedelta(days=days), time(12), RecordAge.TZ)


def backfill(session: Session, *, dry_run: bool, undo: bool = False) -> int:
    # The marker is the authoritative rollback selector, not current orphanhood.
    query = select(IndexRecord).where(IndexRecord.date_generated.is_(True))
    if not undo:
        query = select(IndexRecord).where(
            ~exists().where(Task.task_id == IndexRecord.task_id),
            IndexRecord.uploaded_at.is_(None),
            IndexRecord.date_generated.is_(False),
            IndexRecord.generated_uploaded_at.is_(None),
        )
    count = 0
    for record in session.scalars(query.order_by(IndexRecord.id)).yield_per(500):
        count += 1
        if not dry_run:
            record.generated_uploaded_at = None if undo else generated_time(record.id)
            record.date_generated = not undo
    # Caller owns commit/rollback; dry-run does not mutate the session.
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--undo", action="store_true")
    args = parser.parse_args()
    url = os.environ.get("DATABASE_URL")
    if not url:
        parser.error("DATABASE_URL must be set explicitly; no default database is used")
    database = Database(url)
    try:
        with database.session() as session:
            # Single transaction. Rollback automatically on failure.
            count = backfill(session, dry_run=args.dry_run, undo=args.undo)
            if args.dry_run:
                session.rollback()
            else:
                session.commit()
        print(
            f"{'dry-run' if args.dry_run else 'applied'}: "
            f"{'undo' if args.undo else 'generate'} {count} rows; base={BASE_DATE}; seed={SEED}"
        )
    finally:
        database.engine.dispose()


if __name__ == "__main__":
    main()
