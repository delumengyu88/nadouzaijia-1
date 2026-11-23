import hashlib
import io
import re

from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from nadou.importer import TestTaskFilter
from nadou.models import Task
from nadou.records import IndexRecordStore


class HitListImporter:
    """Index only consensus rows from a hit-list workbook.

    New template sheet: AI共识. Old template sheet: 双引擎共识节目.
    AI分歧 and 非共识节目 are ignored. 类别 is stored when the old sheet has it.
    """

    CONSENSUS_SHEETS = frozenset({"AI共识", "双引擎共识节目"})
    NAME = "节目名称"
    FRAMES = "命中帧数"
    TIMESTAMP = "时间戳"
    CATEGORY = "类别"

    def __init__(self, session: Session) -> None:
        self.session = session

    def import_xlsx(
        self, content: bytes, *, task_id: str, approved_sha256: str | None = None
    ) -> int:
        digest = hashlib.sha256(content).hexdigest()
        if approved_sha256 is not None and not (
            re.fullmatch(r"[0-9a-f]{64}", approved_sha256) and approved_sha256 == digest
        ):
            raise ValueError("工作簿与授权来源清单不匹配")
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        try:
            with self.session.begin_nested():
                task = self.session.scalar(
                    select(Task).where(Task.task_id == task_id).with_for_update()
                )
                if (
                    task is not None
                    and task.approved_hitlist_sha256
                    and approved_sha256 is None
                    and task.approved_hitlist_sha256 != digest
                ):
                    raise ValueError("替换已授权批次需要匹配新的授权清单")
                if approved_sha256 is not None and not TestTaskFilter.commercial_task(task):
                    raise ValueError("授权来源必须对应已完成的本项目非测试任务")
                written = sum(
                    self._import_sheet(sheet, task_id, digest)
                    for sheet in workbook.worksheets
                    if sheet.title in self.CONSENSUS_SHEETS
                )
                if approved_sha256 is not None:
                    if not written:
                        raise ValueError("授权工作簿没有可用共识记录")
                    task.approved_hitlist_sha256 = digest
                return written
        finally:
            workbook.close()

    def _import_sheet(self, sheet, task_id: str, digest: str) -> int:
        rows = sheet.iter_rows(values_only=True)
        try:
            header = next(rows)
        except StopIteration:
            return 0
        columns = {str(cell).strip(): index for index, cell in enumerate(header) if cell}
        if self.NAME not in columns:
            return 0
        store = IndexRecordStore(self.session)
        written = 0
        for row in rows:
            name = self._cell(row, columns, self.NAME)
            if not name:
                continue
            frames = self._frames(self._cell(row, columns, self.FRAMES))
            timestamp = self._cell(row, columns, self.TIMESTAMP)
            category = self._cell(row, columns, self.CATEGORY) or None
            store.upsert(
                task_id=task_id,
                program_name=name,
                program_name_raw=name,
                hit_type="consensus",
                hitlist_sha256=digest,
                frame_count=frames,
                timestamps=[timestamp] if timestamp else [],
                category=category,
            )
            written += 1
        return written

    @staticmethod
    def _cell(row: tuple, columns: dict[str, int], name: str) -> str:
        index = columns.get(name)
        if index is None or index >= len(row) or row[index] is None:
            return ""
        return str(row[index]).strip()

    @staticmethod
    def _frames(value: str) -> int:
        if not value:
            return 0
        try:
            return max(int(float(value)), 0)
        except ValueError:
            return 0


def import_approved(argv: list[str]) -> int:
    """Match a project approval manifest to the workbook before importing it."""
    import argparse
    import csv
    from pathlib import Path

    from nadou.config import get_settings
    from nadou.db import Database

    parser = argparse.ArgumentParser(prog="import-approved-hitlist")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--workbook", required=True, type=Path)
    parser.add_argument("--task-id", required=True)
    args = parser.parse_args(argv)
    with args.manifest.open(encoding="utf-8-sig", newline="") as source:
        rows = csv.DictReader(source)
        if not {"task_id", "sha256"}.issubset(rows.fieldnames or ()):
            raise ValueError("授权来源清单需要 task_id、sha256")
        digests = [row["sha256"] for row in rows if row["task_id"] == args.task_id]
    if len(digests) != 1 or not re.fullmatch(r"[0-9a-f]{64}", digests[0] or ""):
        raise ValueError("清单中缺少该任务的唯一授权摘要")
    data = args.workbook.read_bytes()
    with Database(get_settings().database_url).session() as session, session.begin():
        count = HitListImporter(session).import_xlsx(
            data, task_id=args.task_id, approved_sha256=digests[0]
        )
    print(f"已校验并导入 {count} 行；待复核记录继续隐藏")
    return 0
