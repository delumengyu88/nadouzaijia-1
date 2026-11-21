import io

from openpyxl import load_workbook
from sqlalchemy.orm import Session

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

    def import_xlsx(self, content: bytes, *, task_id: str) -> int:
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        try:
            written = 0
            for sheet in workbook.worksheets:
                if sheet.title not in self.CONSENSUS_SHEETS:
                    continue
                written += self._import_sheet(sheet, task_id)
            return written
        finally:
            workbook.close()

    def _import_sheet(self, sheet, task_id: str) -> int:
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
