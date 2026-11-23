import csv
import io
from dataclasses import dataclass

from sqlalchemy import update
from sqlalchemy.orm import Session

from nadou.filename_parser import FilenameParser
from nadou.models import IndexRecord, Task
from nadou.platform_names import PlatformNames
from nadou.text import RecordAge, TextNormalizer


class TaskExportImportError(ValueError):
    pass


@dataclass(frozen=True)
class ImportResult:
    inserted: int
    updated: int
    test_rows: int


class TestTaskFilter:
    """A filename or platform that starts with test_ never counts."""

    PREFIX = "test_"
    SEED_TASK_IDS = frozenset(
        (
            "a1b2c3d4",
            "b2c3d4e5",
            "c3d4e5f6",
            "d4e5f6a7",
            "e5f6a7b8",
            "f6a7b8c9",
            "a7b8c9d0",
            "b8c9d0e1",
            "c9d0e1f2",
            "e1f2a3b4",
        )
    )

    @classmethod
    def commercial_task(cls, task: Task | None) -> bool:
        return (
            task is not None
            and task.status == "done"
            and not task.is_test
            and not task.task_id.startswith("demo-")
            and task.task_id not in cls.SEED_TASK_IDS
            and not cls.excluded(task.raw_filename, task.platform_name, None)
        )

    @staticmethod
    def excluded(filename: str, platform_raw: str | None, platform_canonical: str | None) -> bool:
        for value in (filename, platform_raw or "", platform_canonical or ""):
            if TextNormalizer.fold(value).startswith(TestTaskFilter.PREFIX):
                return True
        return False


class TaskExportImporter:
    """Idempotent upsert of the hourly task export, keyed by task_id."""

    COLUMNS = ("task_id", "状态", "提交时间", "filename", "公网/专网", "平台名")
    STATUSES = frozenset({"done", "cancelled", "duplicate"})
    NETWORKS = frozenset({"公网", "专网"})

    def __init__(self, session: Session) -> None:
        self.session = session

    def import_csv(self, content: str) -> ImportResult:
        reader = csv.DictReader(io.StringIO(content.lstrip("\ufeff")))
        if reader.fieldnames is None:
            raise TaskExportImportError("CSV has no header")
        header = [name.strip() for name in reader.fieldnames if name and name.strip()]
        missing = [column for column in self.COLUMNS if column not in header]
        if missing:
            raise TaskExportImportError("CSV missing columns: " + ", ".join(missing))

        inserted = 0
        updated = 0
        test_rows = 0
        for line_no, row in enumerate(reader, start=2):
            if all(not (value or "").strip() for value in row.values()):
                continue
            created, is_test = self._upsert(row, line_no)
            if created:
                inserted += 1
            else:
                updated += 1
            if is_test:
                test_rows += 1
        self.session.flush()
        return ImportResult(inserted=inserted, updated=updated, test_rows=test_rows)

    def _upsert(
        self,
        row: dict[str, str | None],
        line_no: int,
    ) -> tuple[bool, bool]:
        task_id = self._cell(row, "task_id")
        if not task_id:
            raise TaskExportImportError(f"line {line_no}: task_id is empty")
        status = self._cell(row, "状态").casefold()
        if status not in self.STATUSES:
            raise TaskExportImportError(f"line {line_no}: unknown status {status!r}")
        submitted = self._cell(row, "提交时间")
        try:
            created_at = RecordAge.parse_submitted(submitted) if submitted else None
        except ValueError as exc:
            raise TaskExportImportError(f"line {line_no}: {exc}") from exc

        filename = self._cell(row, "filename")
        network_cell = self._cell(row, "公网/专网")
        platform_cell = self._cell(row, "平台名")
        parsed = FilenameParser.parse(filename)
        if network_cell:
            network = network_cell if network_cell in self.NETWORKS else None
        else:
            network = parsed.network_type
        platform_raw = platform_cell if platform_cell else parsed.platform
        normalized = PlatformNames.normalize(platform_raw)
        is_test = TestTaskFilter.excluded(filename, platform_raw, normalized.name)

        existing = self.session.get(Task, task_id)
        created = existing is None
        if existing is None:
            existing = Task(task_id=task_id, status=status, raw_filename=filename)
            self.session.add(existing)
        existing.status = status
        existing.created_at = created_at
        existing.raw_filename = filename
        existing.network_type = network
        existing.platform_name = normalized.name
        existing.platform_province = normalized.province
        existing.platform_operator = normalized.operator
        existing.platform_subtype = normalized.subtype
        existing.source_unverified = normalized.unrecognized
        existing.is_test = is_test
        # A re-imported task can supply the real time for previously orphaned
        # records. Clear placeholders atomically in this import transaction so
        # those records can enter exports and evidence prefill again. A task
        # with no real time must not erase the only date we have.
        if existing.created_at is not None or existing.file_uploaded_at is not None:
            self.session.execute(
                update(IndexRecord)
                .where(IndexRecord.task_id == task_id, IndexRecord.date_generated.is_(True))
                .values(generated_uploaded_at=None, date_generated=False)
            )
        return created, is_test

    @staticmethod
    def _cell(row: dict[str, str | None], key: str) -> str:
        value = row.get(key)
        if value is None:
            return ""
        return value.strip()
