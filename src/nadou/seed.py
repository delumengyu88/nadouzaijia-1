import csv
import io
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from nadou.importer import TaskExportImporter
from nadou.models import Entitlement, ProgramAlias, User
from nadou.records import IndexRecordStore
from nadou.text import RecordAge, TextNormalizer

DEMO_VIP_USER_ID = "00000000-0000-4000-8000-000000000001"
PROGRAM = "示例节目"
PROGRAM_ALIAS = "示例剧集"


class DevSeedDisabled(RuntimeError):
    """The fixed demo VIP user is local-only and stays off unless explicitly allowed."""


class Seed:
    """Build a synthetic export and index. No real titles, hosts, or buckets."""

    def __init__(
        self,
        session: Session,
        now: datetime | None = None,
        *,
        allow_dev_user: bool = False,
    ) -> None:
        self.session = session
        self.now = now or datetime.now(RecordAge.TZ)
        self.allow_dev_user = allow_dev_user

    def run(self) -> str:
        self._aliases()
        self.session.flush()
        csv_text = self.build_csv()
        TaskExportImporter(self.session).import_csv(csv_text)
        self._records()
        self._users()
        return csv_text

    def build_csv(self) -> str:
        rows = [
            self._row(
                "a1b2c3d4", "done", 200, "公网-示例视频-电影-{stamp}.mp4", "公网", "示例视频"
            ),
            self._row("b2c3d4e5", "done", 100, "公网-示例视频OTT-电影-{stamp}.mp4", "", ""),
            self._row(
                "c3d4e5f6", "done", 10, "专网-示例专网-电视剧-{stamp}.mp4", "专网", "示例专网"
            ),
            self._row("d4e5f6a7", "done", 50, "unknown.mp4", "", ""),
            self._row(
                "e5f6a7b8", "cancelled", 200, "公网-示例视频-电影-{stamp}.mp4", "公网", "示例视频"
            ),
            self._row(
                "f6a7b8c9", "duplicate", 200, "公网-示例视频-电影-{stamp}.mp4", "公网", "示例视频"
            ),
            self._row("a7b8c9d0", "done", 200, "test_demo.mp4", "公网", "示例视频"),
            self._row(
                "b8c9d0e1", "done", 200, "公网-示例视频-电影-{stamp}.mp4", "公网", "Test_平台"
            ),
            self._row("c9d0e1f2", "done", 40, "1905.mp4", "", ""),
            self._row(
                "e1f2a3b4",
                "done",
                120,
                "公网-其他平台-电影-{stamp}.mp4",
                "专网",
                "示例视频平台",
            ),
        ]
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(TaskExportImporter.COLUMNS)
        writer.writerows(rows)
        return buffer.getvalue()

    def _row(
        self,
        task_id: str,
        status: str,
        days_ago: int,
        filename: str,
        network: str,
        platform: str,
    ) -> list[str]:
        when = self._at(days_ago)
        stamp = when.strftime("%Y.%m.%d")
        return [
            task_id,
            status,
            when.strftime("%Y-%m-%d %H:%M:%S"),
            filename.format(stamp=stamp),
            network,
            platform,
        ]

    def _at(self, days_ago: int) -> datetime:
        day = self.now.astimezone(RecordAge.TZ).date() - timedelta(days=days_ago)
        return datetime(day.year, day.month, day.day, 11, 12, 24, tzinfo=RecordAge.TZ)

    def _aliases(self) -> None:
        self._ensure_alias(ProgramAlias, PROGRAM_ALIAS, PROGRAM)

    def _ensure_alias(self, model, alias: str, canonical: str) -> None:
        folded_alias = TextNormalizer.fold(alias)
        folded_name = TextNormalizer.fold(canonical)
        row = self.session.scalar(select(model).where(model.alias == folded_alias))
        if row is None:
            self.session.add(model(alias=folded_alias, canonical_name=folded_name))
        else:
            row.canonical_name = folded_name

    def _records(self) -> None:
        store = IndexRecordStore(self.session)
        specs = [
            ("a1b2c3d4", "consensus", 3, ["00:01:02", "00:09:40"]),
            ("b2c3d4e5", "dispute", 1, ["00:12:00"]),
            ("c3d4e5f6", "consensus", 1, ["00:00:05"]),
            ("d4e5f6a7", "consensus", 2, ["00:03:10"]),
            ("e5f6a7b8", "consensus", 1, ["00:00:01"]),
            ("f6a7b8c9", "consensus", 1, ["00:00:01"]),
            ("a7b8c9d0", "consensus", 1, ["00:00:01"]),
            ("b8c9d0e1", "consensus", 1, ["00:00:01"]),
            ("c9d0e1f2", "dispute", 1, ["00:08:08"]),
            ("e1f2a3b4", "consensus", 2, ["00:02:02"]),
        ]
        for task_id, hit_type, frames, timestamps in specs:
            store.upsert(
                task_id=task_id,
                program_name=PROGRAM,
                program_name_raw=PROGRAM,
                hit_type=hit_type,
                frame_count=frames,
                timestamps=timestamps,
            )

    def _users(self) -> None:
        if not self.allow_dev_user:
            raise DevSeedDisabled(
                "Refusing to create the dev VIP user. "
                "Set NADOU_ALLOW_DEV_SEED=1 for a local database."
            )
        if self.session.get(User, DEMO_VIP_USER_ID) is None:
            self.session.add(User(id=DEMO_VIP_USER_ID, created_at=self.now))
        expires = self.now + timedelta(days=30)
        existing = self.session.scalar(
            select(Entitlement).where(
                Entitlement.user_id == DEMO_VIP_USER_ID,
                Entitlement.kind == "vip",
            )
        )
        if existing is None:
            self.session.add(
                Entitlement(
                    user_id=DEMO_VIP_USER_ID,
                    kind="vip",
                    program_name=None,
                    record_id=None,
                    expires_at=expires,
                )
            )
        else:
            existing.expires_at = expires


def main() -> None:
    from alembic import command
    from alembic.config import Config

    from nadou.config import get_settings
    from nadou.db import Database

    settings = get_settings()
    if not settings.allow_dev_seed:
        raise SystemExit(
            "Refusing to seed the dev VIP user. Set NADOU_ALLOW_DEV_SEED=1 for a local database."
        )
    command.upgrade(Config("alembic.ini"), "head")
    database = Database(settings.database_url)
    session = database.session()
    try:
        csv_text = Seed(session, allow_dev_user=True).run()
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    print(csv_text)
    print("anonymous:")
    print(
        "curl -s 'http://localhost:8000/api/v1/programs/search?name=%E7%A4%BA%E4%BE%8B%E5%89%A7%E9%9B%86'"
    )
    print("vip:")
    print(
        "curl -s -H 'X-User-Id: "
        + DEMO_VIP_USER_ID
        + "' 'http://localhost:8000/api/v1/programs/search?name=%E7%A4%BA%E4%BE%8B%E5%89%A7%E9%9B%86'"
    )


if __name__ == "__main__":
    main()
