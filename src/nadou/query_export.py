import zipfile
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from urllib.parse import quote

from openpyxl import Workbook
from openpyxl.drawing.image import Image as SheetImage
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter
from sqlalchemy import select
from sqlalchemy.orm import Session

from nadou.entitlements import AccessGrants, EntitlementPolicy
from nadou.frames import DerivedFrameSource, FrameImage, FrameUnavailable
from nadou.importer import TestTaskFilter
from nadou.models import Entitlement, IndexRecord, MemberQuery, QueryProgram, Task, User
from nadou.normalize import ProgramNormalizer
from nadou.platform_names import PlatformNames
from nadou.program_titles import ProgramTitleGuard
from nadou.search import ProgramSearch, UnknownUser
from nadou.text import RecordAge


class UnknownQuery(Exception):
    pass


class ExportForbidden(Exception):
    """The caller is not a live 普通会员 or VIP."""


class ExportLimits:
    MAX_BYTES = 20 * 1024 * 1024


class MembershipGate:
    """Who may export, and which age floor that membership unlocks."""

    @staticmethod
    def live(session: Session, user_id: str | None, now: datetime) -> list[Entitlement]:
        if user_id is None:
            return []
        user = session.get(User, user_id)
        if user is None:
            raise UnknownUser(user_id)
        rows = session.scalars(select(Entitlement).where(Entitlement.user_id == user_id)).all()
        return [row for row in rows if row.expires_at > now]

    @staticmethod
    def tier(rows: list[Entitlement]) -> str | None:
        kinds = {row.kind for row in rows}
        if "vip" in kinds:
            return "vip"
        if "regular" in kinds:
            return "regular"
        return None

    @staticmethod
    def grants(tier: str) -> AccessGrants:
        return AccessGrants(vip=tier == "vip", regular=tier == "regular")


class VisibleSpan:
    """Age label printed in 查询范围. VIP sees 30+, 普通 sees 90+."""

    @staticmethod
    def label(tier: str) -> str:
        floor = 30 if tier == "vip" else 90
        return f"≥{floor} 天记录"


class ScopeLabel:
    @staticmethod
    def build(program_count: int, tier: str) -> str:
        return f"{program_count} 个节目 · 已收录平台 · {VisibleSpan.label(tier)}"


class PlatformLabel:
    """Province+operator in 平台, network and subtype tags in 子类型."""

    @staticmethod
    def cells(task: Task | None) -> tuple[str, str]:
        raw = task.platform_name if task is not None else None
        resolved = PlatformNames.normalize(raw)
        if resolved.unrecognized or not resolved.name:
            return PlatformNames.UNRECOGNIZED, ""
        tags: list[str] = []
        network = task.network_type if task is not None else None
        if network:
            tags.append(network)
        subtype = resolved.subtype
        if subtype is None and task is not None:
            subtype = task.platform_subtype
        if subtype and subtype not in tags:
            tags.append(subtype)
        return resolved.name, " ".join(tags)


class HitColumns:
    """Fixed columns for visible confirmed records."""

    LEAD = ("平台", "子类型", "类别", "节目类型", "上传节目名", "匹配节目名")
    TAIL = ("检测日期", "命中画面截图")


class RestStatus:
    MISS = "未命中"
    FAILED = "失败"


class ShotPlaceholder:
    TEXT = "暂无截图"


class BeijingDate:
    @staticmethod
    def format(when: datetime) -> str:
        return when.astimezone(RecordAge.TZ).strftime("%Y-%m-%d")


class PartNames:
    STEM = "汇总"
    MEASURE_LABEL = "第 999 部分 / 共 999 部分"

    @staticmethod
    def single() -> str:
        return f"{PartNames.STEM}.xlsx"

    @staticmethod
    def part(index: int) -> str:
        return f"{PartNames.STEM}-{index}.xlsx"

    @staticmethod
    def archive() -> str:
        return f"{PartNames.STEM}.zip"

    @staticmethod
    def label(index: int, total: int) -> str:
        return f"第 {index} 部分 / 共 {total} 部分"


@dataclass(frozen=True)
class ExportHeader:
    query_id: str
    scope: str
    generated_on: str


@dataclass(frozen=True)
class HitRow:
    platform: str
    subtype: str
    program_type: str
    uploaded_name: str
    matched_name: str
    checked_on: str
    image: bytes | None
    image_width: int
    image_height: int
    category: str | None = None


@dataclass(frozen=True)
class RestRow:
    uploaded_name: str
    status: str
    detail: str


class ExportFile:
    XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    ZIP = "application/zip"

    def __init__(self, filename: str, media_type: str, body: bytes) -> None:
        self.filename = filename
        self.media_type = media_type
        self.body = body

    def disposition(self) -> str:
        fallback = "export.zip" if self.filename.endswith(".zip") else "export.xlsx"
        encoded = quote(self.filename)
        return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{encoded}"


class WorkbookArchive:
    @staticmethod
    def zip(parts: list[tuple[str, bytes]]) -> bytes:
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, payload in parts:
                archive.writestr(name, payload)
        return buffer.getvalue()


class SheetStyle:
    FONT = "Droid Sans Fallback"

    @staticmethod
    def title() -> Font:
        return Font(name=SheetStyle.FONT, size=16, bold=True)

    @staticmethod
    def header() -> Font:
        return Font(name=SheetStyle.FONT, size=11, bold=True)

    @staticmethod
    def body() -> Font:
        return Font(name=SheetStyle.FONT, size=11)


class HitWorkbook:
    """Build one workbook, or several parts when the file would pass the size limit.

    Parts are named 汇总-1.xlsx, 汇总-2.xlsx, and so on. Every part repeats the
    header block. The unresolved-program sheet is written only on the last part.
    A single record is never split across files. One part is returned as
    汇总.xlsx; several parts are returned as one 汇总.zip.
    """

    HITS = "命中结果"
    REST = "未命中与待处理"
    TITLE = "那豆在架 命中结果"
    REST_COLUMNS = ("上传节目名", "状态", "说明")

    def __init__(self, max_bytes: int = ExportLimits.MAX_BYTES) -> None:
        self.max_bytes = max_bytes

    def render(self, header: ExportHeader, hits: list[HitRow], rest: list[RestRow]) -> ExportFile:
        ranges = self._ranges(header, hits, rest)
        total = len(ranges)
        parts: list[tuple[str, bytes]] = []
        for index, (start, end) in enumerate(ranges, start=1):
            label = None if total == 1 else PartNames.label(index, total)
            sheet_rest = rest if index == total else None
            blob = self._bytes(header, hits[start:end], sheet_rest, label)
            name = PartNames.single() if total == 1 else PartNames.part(index)
            parts.append((name, blob))
        if total == 1:
            return ExportFile(parts[0][0], ExportFile.XLSX, parts[0][1])
        return ExportFile(PartNames.archive(), ExportFile.ZIP, WorkbookArchive.zip(parts))

    def _ranges(
        self,
        header: ExportHeader,
        hits: list[HitRow],
        rest: list[RestRow],
    ) -> list[tuple[int, int]]:
        if not hits:
            return [(0, 0)]
        ranges: list[tuple[int, int]] = []
        start = 0
        count = len(hits)
        while start < count:
            end = self._largest_end(header, hits, rest, start, count)
            ranges.append((start, end))
            start = end
        return ranges

    def _largest_end(
        self,
        header: ExportHeader,
        hits: list[HitRow],
        rest: list[RestRow],
        start: int,
        count: int,
    ) -> int:
        low = start + 1
        high = count
        best = start + 1
        while low <= high:
            mid = (low + high) // 2
            size = self._measured(
                header,
                hits[start:mid],
                rest,
                include_rest=mid == count,
            )
            if size <= self.max_bytes:
                best = mid
                low = mid + 1
            elif mid == start + 1:
                return mid
            else:
                high = mid - 1
        return best

    def _measured(
        self,
        header: ExportHeader,
        hits: list[HitRow],
        rest: list[RestRow],
        *,
        include_rest: bool,
    ) -> int:
        # The measure label is wider than a real part label, so a part that
        # fits here still fits once it is numbered. Sheet 2 is only in the
        # slice that reaches the end, matching the file that is saved.
        payload = rest if include_rest else None
        return len(self._bytes(header, hits, payload, PartNames.MEASURE_LABEL))

    def _bytes(
        self,
        header: ExportHeader,
        hits: list[HitRow],
        rest: list[RestRow] | None,
        part_label: str | None,
    ) -> bytes:
        workbook = Workbook()
        workbook.properties.creator = "那豆在架"
        workbook.properties.title = self.TITLE
        sheet = workbook.active
        sheet.title = self.HITS
        columns = HitColumns.LEAD + HitColumns.TAIL
        self._write_hits(sheet, header, hits, part_label, columns)
        if rest is not None:
            self._write_rest(workbook.create_sheet(self.REST), rest)
        buffer = BytesIO()
        workbook.save(buffer)
        workbook.close()
        return buffer.getvalue()

    def _write_hits(
        self,
        sheet,
        header: ExportHeader,
        hits: list[HitRow],
        part_label: str | None,
        columns: tuple[str, ...],
    ) -> None:
        sheet["A1"] = self.TITLE
        sheet["A1"].font = SheetStyle.title()
        sheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(columns))
        sheet.row_dimensions[1].height = 24
        meta = [
            ("查询编号", header.query_id),
            ("查询范围", header.scope),
            ("生成日期", header.generated_on),
        ]
        if part_label:
            meta.append(("部分", part_label))
        for offset, (label, value) in enumerate(meta, start=2):
            sheet.cell(offset, 1, label).font = SheetStyle.header()
            sheet.cell(offset, 2, value).font = SheetStyle.body()
        header_row = 3 + len(meta)
        for column, name in enumerate(columns, start=1):
            cell = sheet.cell(header_row, column, name)
            cell.font = SheetStyle.header()
            cell.alignment = Alignment(vertical="center", wrap_text=True)
        for index, hit in enumerate(hits):
            row = header_row + 1 + index
            values = self._values(hit, columns)
            for column, value in enumerate(values, start=1):
                cell = sheet.cell(row, column, value)
                cell.font = SheetStyle.body()
                cell.alignment = Alignment(vertical="center", wrap_text=True)
            if hit.image:
                self._embed(sheet, hit, row, len(columns))
        widths = {
            "平台": 18,
            "子类型": 14,
            "类别": 14,
            "节目类型": 12,
            "上传节目名": 18,
            "匹配节目名": 18,
            "检测日期": 14,
            "命中画面截图": 28,
        }
        for column, name in enumerate(columns, start=1):
            sheet.column_dimensions[get_column_letter(column)].width = widths[name]
        sheet.freeze_panes = f"A{header_row + 1}"
        self._page(sheet)

    @staticmethod
    def _values(hit: HitRow, columns: tuple[str, ...]) -> list[str | None]:
        fields = {
            "平台": hit.platform,
            "子类型": hit.subtype or None,
            "类别": hit.category,
            "节目类型": hit.program_type,
            "上传节目名": hit.uploaded_name,
            "匹配节目名": hit.matched_name,
            "检测日期": hit.checked_on,
            "命中画面截图": None if hit.image else ShotPlaceholder.TEXT,
        }
        return [fields[name] for name in columns]

    @staticmethod
    def _embed(sheet, hit: HitRow, row: int, column_count: int) -> None:
        picture = SheetImage(BytesIO(hit.image))
        display_width = 220
        display_height = max(1, round(hit.image_height * display_width / hit.image_width))
        picture.width = display_width
        picture.height = display_height
        column = get_column_letter(column_count)
        sheet.add_image(picture, f"{column}{row}")
        sheet.row_dimensions[row].height = display_height * 0.75 + 8

    def _write_rest(self, sheet, rows: list[RestRow]) -> None:
        for column, name in enumerate(self.REST_COLUMNS, start=1):
            cell = sheet.cell(1, column, name)
            cell.font = SheetStyle.header()
        for index, row in enumerate(rows, start=2):
            sheet.cell(index, 1, row.uploaded_name).font = SheetStyle.body()
            sheet.cell(index, 2, row.status).font = SheetStyle.body()
            sheet.cell(index, 3, row.detail or None).font = SheetStyle.body()
        widths = (24, 12, 36)
        for column, width in enumerate(widths, start=1):
            sheet.column_dimensions[get_column_letter(column)].width = width
        self._page(sheet)

    @staticmethod
    def _page(sheet) -> None:
        sheet.page_setup.orientation = "landscape"
        sheet.page_setup.paperSize = sheet.PAPERSIZE_TABLOID
        sheet.page_setup.fitToWidth = 1
        sheet.page_setup.fitToHeight = 1
        sheet.sheet_properties.pageSetUpPr.fitToPage = True
        sheet.page_margins.left = 0.4
        sheet.page_margins.right = 0.4
        sheet.page_margins.top = 0.45
        sheet.page_margins.bottom = 0.45
        sheet.sheet_view.view = "pageLayout"


class RecordWindow:
    """Same inclusion rules as search, then the membership floor for this export."""

    # Historic resolution values are readable, but never authorize title remapping.
    HIT_RESOLUTIONS = frozenset({"exact", "alias", "manual"})

    @staticmethod
    def permitted(
        task: Task | None,
        record: IndexRecord,
        now: datetime,
        grants: AccessGrants,
    ) -> bool:
        if not ProgramSearch.included(task) or not ProgramSearch.confirmed(record):
            return False
        filename = task.raw_filename if task is not None else ""
        platform_raw = task.platform_name if task is not None else ""
        resolved = PlatformNames.normalize(platform_raw or None)
        if TestTaskFilter.excluded(filename, platform_raw, resolved.name):
            return False
        when = ProgramSearch.record_time(task, record)
        if when is None:
            return False
        age = RecordAge.days(when, now)
        decision = EntitlementPolicy.decide(age, grants)
        return (not decision.locked) and decision.reveal_fields


class QueryExport:
    """Turn one saved query into the caller's workbook."""

    def __init__(
        self,
        session: Session,
        frames: DerivedFrameSource,
        now: datetime,
        max_bytes: int = ExportLimits.MAX_BYTES,
    ) -> None:
        self.session = session
        self.frames = frames
        self.now = now
        self.max_bytes = max_bytes

    def export(self, query_id: str, user_id: str | None) -> ExportFile:
        grants = MembershipGate.live(self.session, user_id, self.now)
        tier = MembershipGate.tier(grants)
        if tier is None:
            raise ExportForbidden(user_id)
        query = self.session.get(MemberQuery, query_id)
        if query is None:
            raise UnknownQuery(query_id)
        items = list(
            self.session.scalars(
                select(QueryProgram)
                .where(QueryProgram.query_id == query.id)
                .order_by(QueryProgram.position)
            ).all()
        )
        hits, rest = self._rows(items, MembershipGate.grants(tier), user_id)
        header = ExportHeader(
            query_id=query.id,
            scope=ScopeLabel.build(len(items), tier),
            generated_on=BeijingDate.format(self.now),
        )
        return HitWorkbook(self.max_bytes).render(header, hits, rest)

    def _rows(
        self,
        items: list[QueryProgram],
        grants: AccessGrants,
        user_id: str | None,
    ) -> tuple[list[HitRow], list[RestRow]]:
        programs = ProgramNormalizer()
        names: set[str] = set()
        for item in items:
            if item.resolution not in RecordWindow.HIT_RESOLUTIONS:
                continue
            canonical = self._title(programs, item)
            if canonical and ProgramTitleGuard.is_billable(canonical):
                names.add(canonical)
        by_program, tasks = self._load(names)
        hits: list[HitRow] = []
        rest: list[RestRow] = []
        for item in items:
            if item.resolution == "pending":
                rest.append(RestRow(item.uploaded_name, RestStatus.FAILED, item.detail or ""))
                continue
            if item.resolution == "failed":
                rest.append(RestRow(item.uploaded_name, RestStatus.FAILED, item.detail or ""))
                continue
            if item.resolution == "miss":
                rest.append(RestRow(item.uploaded_name, RestStatus.MISS, item.detail or ""))
                continue
            canonical = self._title(programs, item)
            visible = self._visible(by_program.get(canonical, []), tasks, grants)
            if not visible:
                rest.append(RestRow(item.uploaded_name, RestStatus.MISS, item.detail or ""))
                continue
            for record, task in visible:
                hits.append(self._hit(item, canonical, record, task, grants, user_id))
        return hits, rest

    def _load(self, names: set[str]) -> tuple[dict[str, list[IndexRecord]], dict[str, Task]]:
        if not names:
            return {}, {}
        records = list(
            self.session.scalars(
                select(IndexRecord).where(IndexRecord.program_name.in_(names))
            ).all()
        )
        grouped: dict[str, list[IndexRecord]] = {}
        for record in records:
            grouped.setdefault(record.program_name, []).append(record)
        task_ids = {record.task_id for record in records}
        tasks: dict[str, Task] = {}
        if task_ids:
            for task in self.session.scalars(select(Task).where(Task.task_id.in_(task_ids))).all():
                tasks[task.task_id] = task
        return grouped, tasks

    @staticmethod
    def _title(programs: ProgramNormalizer, item: QueryProgram) -> str:
        """Use the uploaded title, exactly as search does."""
        return programs.canonical(item.uploaded_name)

    def _visible(
        self,
        records: list[IndexRecord],
        tasks: dict[str, Task],
        grants: AccessGrants,
    ) -> list[tuple[IndexRecord, Task | None]]:
        kept: list[tuple[datetime, IndexRecord, Task | None]] = []
        for record in records:
            task = tasks.get(record.task_id)
            if not RecordWindow.permitted(task, record, self.now, grants):
                continue
            when = ProgramSearch.record_time(task, record)
            if when is None:
                continue
            kept.append((when, record, task))
        kept.sort(key=lambda item: (item[0], item[1].id))
        return [(record, task) for _when, record, task in kept]

    def _hit(
        self,
        item: QueryProgram,
        canonical: str,
        record: IndexRecord,
        task: Task | None,
        grants: AccessGrants,
        user_id: str | None,
    ) -> HitRow:
        when = ProgramSearch.record_time(task, record)
        if when is None:
            raise RuntimeError("permitted record has no detection time")
        age = RecordAge.days(when, self.now)
        decision = EntitlementPolicy.decide(age, grants)
        prepared = self._frame(decision.thumbnail, record.id, user_id)
        platform, subtype = PlatformLabel.cells(task)
        return HitRow(
            platform=platform,
            subtype=subtype,
            category=record.category,
            program_type=item.program_type or "",
            uploaded_name=item.uploaded_name,
            matched_name=canonical,
            checked_on=BeijingDate.format(when),
            image=None if prepared is None else prepared.jpeg,
            image_width=0 if prepared is None else prepared.width,
            image_height=0 if prepared is None else prepared.height,
        )

    def _frame(self, variant: str | None, record_id: str, user_id: str | None):
        if not variant:
            return None
        try:
            raw = self.frames.fetch_representative(variant, record_id, user_id)
        except FrameUnavailable:
            return None
        if not raw:
            return None
        return FrameImage.prepare(raw)
