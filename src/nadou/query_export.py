import zipfile
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from urllib.parse import quote

from openpyxl import Workbook
from openpyxl.drawing.image import Image as SheetImage
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from sqlalchemy import select
from sqlalchemy.orm import Session

from nadou.admin.phone import PhoneMask
from nadou.bands import BAND_KIND, BAND_PRODUCT, BAND_REPORT_LABELS
from nadou.config import get_settings
from nadou.entitlements import AccessGrants, EntitlementPolicy
from nadou.frames import DerivedFrameSource, FrameImage, FrameUnavailable
from nadou.importer import TestTaskFilter
from nadou.models import Entitlement, IndexRecord, MemberQuery, QueryProgram, Task, User
from nadou.normalize import ProgramNormalizer
from nadou.platform_names import PlatformNames
from nadou.program_titles import ProgramTitleGuard
from nadou.search import ProgramSearch, UnknownUser
from nadou.summary_source import approved_summary
from nadou.text import RecordAge


class UnknownQuery(Exception):
    pass


class ExportForbidden(Exception):
    """Nothing in this query may be exported, e.g. every program is summary-only."""


class ExportLimits:
    MAX_BYTES = 20 * 1024 * 1024


class MembershipGate:
    """Which membership the caller holds. Everyone may export; the tier sets the rows.

    Free users get the 180天以上 band with blurred pictures. 普通会员 add 90–180天,
    VIP add 30–90天. A one-program band unlock adds that band for that program.
    """

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
    def tier(rows: list[Entitlement]) -> str:
        kinds = {row.kind for row in rows}
        if "vip" in kinds:
            return "vip"
        if "regular" in kinds:
            return "regular"
        return "free"

    @staticmethod
    def grants(tier: str, rows: list[Entitlement] | None = None, program: str = "") -> AccessGrants:
        """Membership plus this program's band unlocks. Legacy program grants stay online-only."""
        owned = {row.kind for row in rows or [] if row.program_name == program and program}
        return AccessGrants(
            vip=tier == "vip",
            regular=tier == "regular",
            band_90_180=BAND_KIND["d90_179"] in owned,
            band_30_90=BAND_KIND["d30_89"] in owned,
        )


class VisibleSpan:
    """Age label printed in 查询范围. VIP sees 30+, 普通 sees 90+, free sees 180+."""

    @staticmethod
    def label(tier: str) -> str:
        floor = 30 if tier == "vip" else 90 if tier == "regular" else 180
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
            return PlatformNames.OTHER, ""
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
    """Fixed columns, in the order of the 命中结果 template plus 网络类型 and 命中帧数."""

    LEAD = ("序号", "平台", "网络类型", "节目类型", "上传节目名", "匹配节目名", "匹配度")
    TAIL = ("检测日期", "命中帧数", "命中画面截图")
    ALL = LEAD + TAIL
    WIDTHS = {
        "序号": 7,
        "平台": 23.77,
        "网络类型": 10,
        "节目类型": 12.82,
        "上传节目名": 21.1,
        "匹配节目名": 21.1,
        "匹配度": 11.68,
        "检测日期": 15.56,
        "命中帧数": 10,
        "命中画面截图": 35.25,
    }


class MatchDegree:
    """Search matches the normalized title exactly, so every exported hit is 100%."""

    EXACT = 1.0
    FORMAT = "0%"


class RestStatus:
    MISS = "未命中"
    PENDING = "检索中"
    FAILED = "失败"


class ShotPlaceholder:
    TEXT = "暂无截图"
    LOCKED = "解锁后可见"


class BeijingDate:
    @staticmethod
    def format(when: datetime) -> str:
        return when.astimezone(RecordAge.TZ).strftime("%Y-%m-%d")


class ExportCount:
    """How many hit rows were actually exported. Printed in A1 and the file name.

    Rows with a generated date are excluded from export, so this can be lower
    than the count shown in the mini program.
    """

    @staticmethod
    def label(count: int) -> str:
        return f"共导出 {count} 条"

    @staticmethod
    def stem(count: int) -> str:
        return f"共导出{count}条"


class PartNames:
    STEM = "汇总"
    MEASURE_LABEL = "第 999 部分 / 共 999 部分"

    @staticmethod
    def single(count: int | None = None) -> str:
        if count is None:
            return f"{PartNames.STEM}.xlsx"
        return f"{PartNames.STEM}-{ExportCount.stem(count)}.xlsx"

    @staticmethod
    def part(index: int) -> str:
        return f"{PartNames.STEM}-{index}.xlsx"

    @staticmethod
    def archive(count: int | None = None) -> str:
        if count is None:
            return f"{PartNames.STEM}.zip"
        return f"{PartNames.STEM}-{ExportCount.stem(count)}.zip"

    @staticmethod
    def label(index: int, total: int) -> str:
        return f"第 {index} 部分 / 共 {total} 部分"


@dataclass(frozen=True)
class ExportHeader:
    query_id: str
    scope: str
    generated_on: str
    customer: str = ""
    tier: str = ""


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
    network: str = ""
    frame_count: int | None = None
    serial: int = 0
    # clear for members and unlocked bands, blur for the free 180天以上 band.
    image_view: str = ""


@dataclass(frozen=True)
class BandRow:
    """One line standing in for every record of a band the caller cannot see.

    e.g. 「90–180 天 · 12 条 · 待解锁」. 30天内 points to 委托取证 instead.
    """

    uploaded_name: str
    matched_name: str
    program_type: str
    band: str
    count: int

    @property
    def evidence(self) -> bool:
        return self.band == "lt_30"

    @property
    def text(self) -> str:
        label = BAND_REPORT_LABELS[self.band]
        state = "可委托取证" if self.evidence else "待解锁"
        return f"{label} · {self.count} 条 · {state}"

    def note(self, tier: str) -> str:
        if self.evidence:
            return "不在线展示，可在小程序「委托取证」申请取证"
        from nadou.payments.catalog import product_by_code

        product = product_by_code(BAND_PRODUCT[self.band])
        price = ""
        if product is not None:
            yuan, fen = divmod(product.amount_fen, 100)
            price = f"¥{yuan}" if fen == 0 else f"¥{yuan}.{fen:02d}".rstrip("0")
        if self.band == "d30_89":
            upgrade = "升级 VIP " if tier == "regular" else "开通 VIP "
        else:
            upgrade = "开通会员"
        return f"本节目单次解锁 {price}，或{upgrade}后查看"


ExportRow = HitRow | BandRow


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
    """Colors and fonts of the 命中结果 template."""

    FONT = "Droid Sans Fallback"
    INK = "FF111111"
    TEXT = "FF222222"
    MUTED = "FF666666"
    WHITE = "FFFFFFFF"
    BAND_FILL = "FFF3F5F8"
    LOCK_TEXT = "FF1D4E9E"

    @staticmethod
    def title() -> Font:
        return Font(name=SheetStyle.FONT, size=16, bold=True, color=SheetStyle.INK)

    @staticmethod
    def header() -> Font:
        return Font(name=SheetStyle.FONT, size=11, bold=True, color=SheetStyle.WHITE)

    @staticmethod
    def label() -> Font:
        return Font(name=SheetStyle.FONT, size=11, color=SheetStyle.MUTED)

    @staticmethod
    def note() -> Font:
        return Font(name=SheetStyle.FONT, size=10, color=SheetStyle.MUTED)

    @staticmethod
    def body() -> Font:
        return Font(name=SheetStyle.FONT, size=11, color=SheetStyle.TEXT)

    @staticmethod
    def band() -> Font:
        return Font(name=SheetStyle.FONT, size=11, bold=True, color=SheetStyle.LOCK_TEXT)

    @staticmethod
    def header_fill() -> PatternFill:
        return PatternFill("solid", fgColor=SheetStyle.INK)

    @staticmethod
    def band_fill() -> PatternFill:
        return PatternFill("solid", fgColor=SheetStyle.BAND_FILL)

    @staticmethod
    def border() -> Border:
        side = Side(style="thin", color="FFD0D5DD")
        return Border(left=side, right=side, top=side, bottom=side)


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

    @classmethod
    def first_row(cls, exported_count: int) -> str:
        return f"{cls.TITLE}（{ExportCount.label(exported_count)}）"

    # Template geometry: a thin spacer row, the title, two meta rows, a note row,
    # another spacer, then the header. Data starts on HEADER_ROW + 1.
    TITLE_ROW = 2
    META_ROWS = (3, 4)
    NOTE_ROW = 5
    HEADER_ROW = 7
    NOTE = (
        "结果说明：按检测日期分档；未开通的时间段只汇总条数。"
        "30 天内只显示条数和委托取证入口，不展示明细。"
    )

    @staticmethod
    def exported(rows: list) -> int:
        return sum(1 for row in rows if isinstance(row, HitRow))

    def render(
        self, header: ExportHeader, hits: list[ExportRow], rest: list[RestRow]
    ) -> ExportFile:
        ranges = self._ranges(header, hits, rest)
        total = len(ranges)
        parts: list[tuple[str, bytes]] = []
        for index, (start, end) in enumerate(ranges, start=1):
            label = None if total == 1 else PartNames.label(index, total)
            sheet_rest = rest if index == total else None
            blob = self._bytes(header, hits[start:end], sheet_rest, label)
            name = PartNames.single() if total == 1 else PartNames.part(index)
            parts.append((name, blob))
        count = self.exported(hits)
        if total == 1:
            return ExportFile(PartNames.single(count), ExportFile.XLSX, parts[0][1])
        return ExportFile(PartNames.archive(count), ExportFile.ZIP, WorkbookArchive.zip(parts))

    def _ranges(
        self,
        header: ExportHeader,
        hits: list[ExportRow],
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
        hits: list[ExportRow],
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
        hits: list[ExportRow],
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
        hits: list[ExportRow],
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
        hits: list[ExportRow],
        part_label: str | None,
        columns: tuple[str, ...],
    ) -> None:
        last = len(columns)
        sheet.sheet_view.showGridLines = False
        for row, height in ((1, 9), (self.TITLE_ROW, 27), (6, 9), (self.HEADER_ROW, 27)):
            sheet.row_dimensions[row].height = height
        for row in (*self.META_ROWS, self.NOTE_ROW):
            sheet.row_dimensions[row].height = 18.75
        title = sheet.cell(self.TITLE_ROW, 1, self.first_row(self.exported(hits)))
        title.font = SheetStyle.title()
        title.alignment = Alignment(horizontal="left", vertical="center")
        sheet.merge_cells(
            start_row=self.TITLE_ROW, start_column=1, end_row=self.TITLE_ROW, end_column=last
        )
        half = 5
        meta = (
            (self.META_ROWS[0], ("查询编号", header.query_id), ("客户", header.customer or "—")),
            (self.META_ROWS[1], ("查询范围", header.scope), ("生成日期", header.generated_on)),
        )
        for row, left, right in meta:
            for start, end, (label, value) in ((1, half, left), (half + 1, half * 2, right)):
                cell = sheet.cell(row, start, f"{label}：{value}")
                cell.font = SheetStyle.label()
                cell.alignment = Alignment(horizontal="left", vertical="center")
                sheet.merge_cells(start_row=row, start_column=start, end_row=row, end_column=end)
        note = self.NOTE if not part_label else f"{self.NOTE}（{part_label}）"
        cell = sheet.cell(self.NOTE_ROW, 1, note)
        cell.font = SheetStyle.note()
        cell.alignment = Alignment(horizontal="left", vertical="center")
        sheet.merge_cells(
            start_row=self.NOTE_ROW, start_column=1, end_row=self.NOTE_ROW, end_column=last
        )
        border = SheetStyle.border()
        for column, name in enumerate(columns, start=1):
            cell = sheet.cell(self.HEADER_ROW, column, name)
            cell.font = SheetStyle.header()
            cell.fill = SheetStyle.header_fill()
            cell.border = border
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        for index, hit in enumerate(hits):
            row = self.HEADER_ROW + 1 + index
            if isinstance(hit, BandRow):
                self._write_band(sheet, hit, row, columns, header)
                continue
            values = self._values(hit, columns)
            for column, value in enumerate(values, start=1):
                cell = sheet.cell(row, column, value)
                cell.font = SheetStyle.body()
                cell.border = border
                centered = columns[column - 1] not in ("上传节目名", "匹配节目名")
                cell.alignment = Alignment(
                    horizontal="center" if centered else "left",
                    vertical="center",
                    wrap_text=True,
                )
                if columns[column - 1] == "匹配度":
                    cell.number_format = MatchDegree.FORMAT
            sheet.row_dimensions[row].height = 22
            if hit.image:
                self._embed(sheet, hit, row, len(columns))
        for column, name in enumerate(columns, start=1):
            sheet.column_dimensions[get_column_letter(column)].width = HitColumns.WIDTHS[name]
        sheet.freeze_panes = f"A{self.HEADER_ROW + 1}"
        self._page(sheet)

    # Band rows: 平台 to 节目类型 carry the band text, 匹配度 to 截图 carry the next step.
    BAND_TEXT_SPAN = ("平台", "节目类型")
    BAND_NOTE_SPAN = ("匹配度", "命中画面截图")

    def _write_band(self, sheet, band: BandRow, row: int, columns: tuple[str, ...], header) -> None:
        border = SheetStyle.border()
        fill = SheetStyle.band_fill()
        tier = header.tier
        values = {
            "上传节目名": band.uploaded_name,
            "匹配节目名": band.matched_name,
        }
        for column, name in enumerate(columns, start=1):
            cell = sheet.cell(row, column, values.get(name))
            cell.font = SheetStyle.body()
            cell.fill = fill
            cell.border = border
            cell.alignment = Alignment(vertical="center", wrap_text=True)
        text_from = columns.index(self.BAND_TEXT_SPAN[0]) + 1
        text_to = columns.index(self.BAND_TEXT_SPAN[1]) + 1
        note_from = columns.index(self.BAND_NOTE_SPAN[0]) + 1
        note_to = columns.index(self.BAND_NOTE_SPAN[1]) + 1
        text = sheet.cell(row, text_from, band.text)
        text.font = SheetStyle.band()
        text.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        note = sheet.cell(row, note_from, band.note(tier))
        note.font = SheetStyle.note()
        note.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        sheet.merge_cells(start_row=row, start_column=text_from, end_row=row, end_column=text_to)
        sheet.merge_cells(start_row=row, start_column=note_from, end_row=row, end_column=note_to)
        sheet.row_dimensions[row].height = 26

    @staticmethod
    def _values(hit: HitRow, columns: tuple[str, ...]) -> list:
        platform = hit.platform if not hit.subtype else f"{hit.platform} {hit.subtype}"
        fields = {
            "序号": hit.serial or None,
            "平台": platform,
            "网络类型": hit.network or None,
            "节目类型": hit.program_type or None,
            "上传节目名": hit.uploaded_name,
            "匹配节目名": hit.matched_name,
            "匹配度": MatchDegree.EXACT,
            "检测日期": hit.checked_on,
            "命中帧数": hit.frame_count,
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
        if not RecordWindow.eligible(task, record):
            return False
        when = ProgramSearch.record_time(task, record)
        age = RecordAge.days(when, now)
        decision = EntitlementPolicy.decide(age, grants)
        return (not decision.locked) and decision.reveal_fields

    @staticmethod
    def eligible(task: Task | None, record: IndexRecord) -> bool:
        """Inclusion rules shared by visible rows and the per-band locked counts."""
        if not ProgramSearch.visible(record, task):
            return False
        # Generated placeholder dates never enter an export or formal report.
        if not ProgramSearch.reportable(record):
            return False
        filename = task.raw_filename if task is not None else ""
        platform_raw = task.platform_name if task is not None else ""
        resolved = PlatformNames.normalize(platform_raw or None)
        if TestTaskFilter.excluded(filename, platform_raw, resolved.name):
            return False
        return ProgramSearch.record_time(task, record) is not None


class QueryExport:
    """Turn one saved query into the caller's workbook."""

    def __init__(
        self,
        session: Session,
        frames: DerivedFrameSource | None,
        now: datetime,
        max_bytes: int = ExportLimits.MAX_BYTES,
    ) -> None:
        self.session = session
        self.frames = frames
        self.now = now
        self.max_bytes = max_bytes

    def export(
        self, query_id: str, user_id: str | None, *, program_name: str | None = None
    ) -> ExportFile:
        query = self.session.get(MemberQuery, query_id)
        # No owner means the row cannot be attributed. Reject it before the
        # membership gate so every caller gets 404. Allowing it would let
        # anyone who knows the id export it.
        if query is not None and query.user_id is None:
            raise UnknownQuery(query_id)
        grants = MembershipGate.live(self.session, user_id, self.now)
        tier = MembershipGate.tier(grants)
        if query is None or query.user_id != user_id:
            raise UnknownQuery(query_id)
        selection = select(QueryProgram).where(QueryProgram.query_id == query.id)
        if program_name is not None:
            selection = selection.where(QueryProgram.uploaded_name == program_name)
        items = list(self.session.scalars(selection.order_by(QueryProgram.position)))
        if program_name is not None and not items:
            raise UnknownQuery(query_id)
        hits, rest = self._rows(items, tier, grants, user_id)
        header = ExportHeader(
            query_id=query.id,
            scope=ScopeLabel.build(len(items), tier),
            generated_on=BeijingDate.format(self.now),
            customer=self._customer(user_id),
            tier=tier,
        )
        return HitWorkbook(self.max_bytes).render(header, hits, rest)

    def _customer(self, user_id: str | None) -> str:
        user = self.session.get(User, user_id) if user_id else None
        if user is None:
            return ""
        if user.display_name:
            return user.display_name
        return PhoneMask.apply(user.phone) or "微信用户"

    def _rows(
        self,
        items: list[QueryProgram],
        tier: str,
        owned: list[Entitlement],
        user_id: str | None,
    ) -> tuple[list[ExportRow], list[RestRow]]:
        programs = ProgramNormalizer()
        names: set[str] = set()
        for item in items:
            canonical = self._title(programs, item)
            if canonical and ProgramTitleGuard.is_billable(canonical):
                names.add(canonical)
        by_program, tasks = self._load(names)
        restricted = {
            name
            for name, records in by_program.items()
            if any(approved_summary(row, tasks.get(row.task_id)) for row in records)
            and not any(ProgramSearch.visible(row, tasks.get(row.task_id)) for row in records)
        }
        if items and all(self._title(programs, item) in restricted for item in items):
            raise ExportForbidden("该节目仅授权在线展示摘要")
        hits: list[ExportRow] = []
        rest: list[RestRow] = []
        serial = 0
        for item in items:
            if self._title(programs, item) in restricted:
                rest.append(RestRow(item.uploaded_name, "仅限在线摘要", "未提供报告下载"))
                continue
            if item.resolution == "pending":
                rest.append(RestRow(item.uploaded_name, RestStatus.PENDING, item.detail or ""))
                continue
            if item.resolution == "failed":
                rest.append(RestRow(item.uploaded_name, RestStatus.FAILED, item.detail or ""))
                continue
            if item.resolution == "miss" and get_settings().demo_data:
                rest.append(RestRow(item.uploaded_name, RestStatus.MISS, item.detail or ""))
                continue
            canonical = self._title(programs, item)
            grants = MembershipGate.grants(tier, owned, canonical)
            records = by_program.get(canonical, [])
            visible = self._visible(records, tasks, grants)
            locked = self._locked_bands(records, tasks, grants)
            if not visible and not any(locked.values()):
                if ProgramSearch.empty_outcome() == "unverified":
                    rest.append(RestRow(item.uploaded_name, "待核验", "数据待核验"))
                else:
                    rest.append(RestRow(item.uploaded_name, RestStatus.MISS, item.detail or ""))
                continue
            for record, task in visible:
                serial += 1
                hits.append(self._hit(item, canonical, record, task, grants, user_id, serial))
            for band, count in locked.items():
                if count:
                    hits.append(
                        BandRow(
                            uploaded_name=item.uploaded_name,
                            matched_name=canonical,
                            program_type=item.program_type or "",
                            band=band,
                            count=count,
                        )
                    )
        return hits, rest

    def _locked_bands(
        self,
        records: list[IndexRecord],
        tasks: dict[str, Task],
        grants: AccessGrants,
    ) -> dict[str, int]:
        """Exportable-quality records the caller may not see, counted per band."""
        counts = {"d90_179": 0, "d30_89": 0, "lt_30": 0}
        for record in records:
            task = tasks.get(record.task_id)
            if not RecordWindow.eligible(task, record):
                continue
            age = RecordAge.days(ProgramSearch.record_time(task, record), self.now)
            decision = EntitlementPolicy.decide(age, grants)
            if not decision.locked and decision.reveal_fields:
                continue
            if decision.age_bucket in counts:
                counts[decision.age_bucket] += 1
        return counts

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
        # 其他平台 rows go last, as in the mini program.
        kept.sort(
            key=lambda item: (
                PlatformLabel.cells(item[2])[0] == PlatformNames.OTHER,
                item[0],
                item[1].id,
            )
        )
        return [(record, task) for _when, record, task in kept]

    def _hit(
        self,
        item: QueryProgram,
        canonical: str,
        record: IndexRecord,
        task: Task | None,
        grants: AccessGrants,
        user_id: str | None,
        serial: int = 0,
    ) -> HitRow:
        when = ProgramSearch.record_time(task, record)
        if when is None:
            raise RuntimeError("permitted record has no detection time")
        age = RecordAge.days(when, self.now)
        decision = EntitlementPolicy.decide(age, grants)
        view = "clear" if decision.access == "full" else "blur"
        prepared = self._frame(decision.thumbnail, record.id, user_id, blur=view == "blur")
        platform, subtype = PlatformLabel.cells(task)
        network = task.network_type if task is not None and task.network_type else ""
        if platform == PlatformNames.OTHER:
            subtype = ""
        elif network and subtype.startswith(network):
            # 网络类型 has its own column; 平台 keeps only the subtype tag.
            subtype = subtype[len(network) :].strip()
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
            network=network,
            frame_count=record.frame_count,
            serial=serial,
            image_view=view if prepared is not None else "",
        )

    def _frame(self, variant: str | None, record_id: str, user_id: str | None, *, blur: bool):
        if not variant or self.frames is None:
            return None
        try:
            raw = self.frames.fetch_representative(variant, record_id, user_id)
        except FrameUnavailable:
            return None
        if not raw:
            return None
        # Free-tier rows embed only the blurred picture; the clear one is never encoded.
        return FrameImage.blurred(raw) if blur else FrameImage.prepare(raw)
