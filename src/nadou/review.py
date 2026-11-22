import csv
import json
import sys
from typing import TextIO

from sqlalchemy import select
from sqlalchemy.orm import Session

from nadou.models import IndexRecord, ReviewProgramTitle, Task
from nadou.normalize import ProgramNormalizer
from nadou.platform_names import PlatformNames


class ReviewTitles:
    """Exact canonical titles, configured in the database, never a length heuristic."""

    @staticmethod
    def load(session: Session) -> frozenset[str]:
        return frozenset(
            ProgramNormalizer.canonical(name)
            for name in session.scalars(select(ReviewProgramTitle.program_name))
        )


class ReviewExport:
    COLUMNS = ("task_id", "片名", "平台", "命中帧数", "时间戳")

    @staticmethod
    def _cell(value: str) -> str:
        # CSV can be opened in Excel. Keep source text from becoming a formula.
        return "'" + value if value.startswith(("=", "+", "-", "@", "\t", "\r")) else value

    @classmethod
    def write(cls, session: Session, output: TextIO) -> int:
        writer = csv.writer(output)
        writer.writerow(cls.COLUMNS)
        statement = (
            select(IndexRecord, Task.platform_name)
            .outerjoin(Task, Task.task_id == IndexRecord.task_id)
            .where(IndexRecord.hit_type == "review")
            .order_by(IndexRecord.task_id, IndexRecord.program_name, IndexRecord.id)
            .execution_options(yield_per=500)
        )
        count = 0
        for record, platform in session.execute(statement):
            writer.writerow(
                (
                    cls._cell(record.task_id),
                    cls._cell(record.program_name),
                    cls._cell(platform or PlatformNames.UNRECOGNIZED),
                    record.frame_count,
                    json.dumps(record.timestamps or [], ensure_ascii=False),
                )
            )
            count += 1
        return count


class ReviewExportCommand:
    @staticmethod
    def run() -> int:
        from nadou.config import get_settings
        from nadou.db import Database

        database = Database(get_settings().database_url)
        try:
            with database.session() as session:
                ReviewExport.write(session, sys.stdout)
        finally:
            database.engine.dispose()
        return 0
