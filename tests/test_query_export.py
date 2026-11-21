import zipfile
from datetime import UTC, datetime, timedelta
from io import BytesIO
from urllib.parse import quote

from fastapi.testclient import TestClient
from openpyxl import load_workbook
from PIL import Image, ImageDraw

from nadou.db import Database
from nadou.frames import (
    FrameImage,
    MappedFrameReader,
    SignedDerivedFrameSource,
    SyntheticFrame,
)
from nadou.main import create_app
from nadou.models import (
    Entitlement,
    IndexRecord,
    MemberQuery,
    ProgramAlias,
    QueryProgram,
    Task,
    User,
)
from nadou.platform_names import PlatformNames
from nadou.query_export import HitColumns, PartNames, RestStatus
from nadou.signed_url import FakeSignedUrlProvider
from nadou.text import RecordAge

COLUMNS = (
    "平台",
    "节目类型",
    "上传节目名",
    "匹配节目名",
    "检测日期",
    "命中画面截图",
)


def assert_no_match_labels(sheet):
    banned = {
        "匹配方式",
        "命中类型",
        "名称相近，请核对",
        "疑似命中·待核实",
        "精确",
        "别名",
        "人工确认",
    }
    for row in sheet.iter_rows():
        for cell in row:
            assert cell.value not in banned
            if isinstance(cell.value, str):
                assert "名称相近" not in cell.value
                assert "疑似命中" not in cell.value
            assert cell.fill.fill_type is None


SCHEMA_HOSTS = (
    "http://schemas.openxmlformats.org",
    "http://schemas.microsoft.com",
    "http://www.w3.org",
    "http://purl.org",
)


class RecordingSigner:
    def __init__(self) -> None:
        self.inner = FakeSignedUrlProvider()
        self.calls: list[tuple[str, str, str | None]] = []
        self.urls: list[str] = []

    def sign_derived(self, variant: str, record_id: str, user_id: str | None) -> str:
        url = self.inner.sign_derived(variant, record_id, user_id)
        self.calls.append((variant, record_id, user_id))
        self.urls.append(url)
        return url


def add_user(session, user_id, clock, kind=None, program=None, record_id=None, expired=False):
    if session.get(User, user_id) is None:
        session.add(User(id=user_id, created_at=clock))
    if kind is None:
        return
    expires = clock - timedelta(days=1) if expired else clock + timedelta(days=30)
    session.add(
        Entitlement(
            user_id=user_id,
            kind=kind,
            program_name=program,
            record_id=record_id,
            expires_at=expires,
        )
    )


def add_record(
    session,
    *,
    task_id,
    record_id,
    program,
    when,
    status="done",
    platform="示例视频",
    network="专网",
    unverified=False,
    is_test=False,
    filename=None,
    with_task=True,
    hit_type="consensus",
    uploaded_at=None,
):
    if with_task:
        session.add(
            Task(
                task_id=task_id,
                status=status,
                created_at=when,
                file_uploaded_at=uploaded_at,
                raw_filename=filename if filename is not None else f"{task_id}.mp4",
                network_type=None if unverified else network,
                platform_name=None if unverified else platform,
                source_unverified=unverified,
                is_test=is_test,
            )
        )
    session.add(
        IndexRecord(
            id=record_id,
            task_id=task_id,
            program_name=program,
            program_name_raw=program,
            hit_type=hit_type,
            frame_count=1,
            timestamps=["00:01:02"],
            uploaded_at=None if with_task else when,
        )
    )


def add_query(session, clock, query_id, programs):
    session.add(MemberQuery(id=query_id, created_at=clock))
    for position, item in enumerate(programs):
        session.add(
            QueryProgram(
                query_id=query_id,
                position=position,
                uploaded_name=item["uploaded"],
                program_type=item.get("program_type"),
                matched_name=item.get("matched"),
                resolution=item["resolution"],
                detail=item.get("detail"),
            )
        )


def at_age(clock, days, hour=11):
    day = clock.astimezone(RecordAge.TZ).date() - timedelta(days=days)
    return datetime(day.year, day.month, day.day, hour, 0, 0, tzinfo=RecordAge.TZ)


def make_client(session, clock, frames=None, max_bytes=None, urls=None):
    app = create_app(
        database=Database(engine=session.get_bind()),
        urls=urls,
        now=lambda: clock,
        frames=frames,
        export_max_bytes=max_bytes,
    )
    return TestClient(app)


def export(client, query_id, user_id=None):
    headers = {"X-User-Id": user_id} if user_id else {}
    return client.get(f"/api/v1/queries/{query_id}/export", headers=headers)


def meta_block(sheet):
    found = {}
    for row in sheet.iter_rows(max_row=8, max_col=2):
        label = row[0].value
        if label in {"查询编号", "查询范围", "生成日期", "部分"}:
            found[label] = row[1].value
    return found


def hit_table(sheet):
    header_at = None
    headers = None
    for row in sheet.iter_rows(max_col=len(COLUMNS)):
        if row[0].value == "平台":
            header_at = row[0].row
            headers = tuple(cell.value for cell in row)
            break
    assert header_at is not None
    records = []
    styled = []
    for row in sheet.iter_rows(min_row=header_at + 1, max_col=len(COLUMNS)):
        if row[0].value is None:
            break
        records.append(tuple(cell.value for cell in row))
        styled.append(row)
    return headers, records, styled


def rest_table(sheet):
    rows = []
    for index, row in enumerate(sheet.iter_rows(max_col=3), start=1):
        values = tuple(cell.value for cell in row)
        if index == 1:
            assert values == ("上传节目名", "状态", "说明")
            continue
        if values[0] is None:
            break
        rows.append(values)
    return rows


def workbook_parts(response):
    media = response.headers["content-type"]
    if media.startswith(ExportMedia.XLSX):
        return [(PartNames.single(), response.content)]
    assert media.startswith("application/zip")
    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        return [(name, archive.read(name)) for name in archive.namelist()]


class ExportMedia:
    XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def assert_no_hyperlinks(blob: bytes):
    with zipfile.ZipFile(BytesIO(blob)) as archive:
        for name in archive.namelist():
            if not name.endswith((".xml", ".rels")):
                continue
            text = archive.read(name).decode("utf-8")
            lowered = text.lower()
            assert "hyperlink" not in lowered, name
            assert "signed://" not in lowered, name
            cleaned = text
            for host in SCHEMA_HOSTS:
                cleaned = cleaned.replace(host, "")
            assert "http://" not in cleaned, name
            assert "https://" not in cleaned, name
    workbook = load_workbook(BytesIO(blob))
    try:
        for sheet in workbook.worksheets:
            for row in sheet.iter_rows():
                for cell in row:
                    assert cell.hyperlink is None
    finally:
        workbook.close()


def embedded_images(blob: bytes) -> list[bytes]:
    with zipfile.ZipFile(BytesIO(blob)) as archive:
        names = sorted(name for name in archive.namelist() if name.startswith("xl/media/"))
        return [archive.read(name) for name in names]


def solid_png(color: tuple[int, int, int], size=(800, 450)) -> bytes:
    image = Image.new("RGB", size, color)
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 20, size[0] - 20, size[1] - 20), outline=(255, 255, 0), width=6)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def assert_watermark(jpeg: bytes):
    image = Image.open(BytesIO(jpeg))
    assert image.format == "JPEG"
    assert image.width == FrameImage.WIDTH
    assert len(jpeg) <= FrameImage.TARGET_BYTES
    corner = image.crop((image.width - 240, image.height - 120, image.width - 4, image.height - 4))
    pixels = corner.load()
    white = 0
    for y in range(corner.height):
        for x in range(corner.width):
            red, green, blue = pixels[x, y][:3]
            if red > 230 and green > 230 and blue > 230:
                white += 1
    assert white > 20


def test_rejects_callers_who_cannot_export(session, clock, client):
    add_user(session, "user-regular", clock, "regular")
    add_user(session, "user-vip", clock, "vip")
    add_user(session, "user-free", clock)
    add_user(session, "user-p90", clock, "program_90", program="海边故事")
    add_user(session, "user-p30", clock, "program_30", program="海边故事")
    add_user(session, "user-shot", clock, "screenshot", record_id="rec-1")
    add_user(session, "user-expired", clock, "vip", expired=True)
    add_user(session, "user-expired-regular", clock, "regular", expired=True)
    add_user(session, "user-both-expired", clock, "vip", expired=True)
    add_user(session, "user-both-expired", clock, "program_90", program="海边故事")
    add_query(
        session,
        clock,
        "q-auth",
        [{"uploaded": "海边故事", "matched": "海边故事", "resolution": "exact"}],
    )
    session.commit()

    denied = (
        None,
        "user-free",
        "user-p90",
        "user-p30",
        "user-shot",
        "user-expired",
        "user-expired-regular",
        "user-both-expired",
    )
    for user_id in denied:
        response = export(client, "q-auth", user_id)
        assert response.status_code == 403, user_id
        assert not response.content.startswith(b"PK")
        assert "命中结果" not in response.text

    missing_user = export(client, "q-auth", "missing-user")
    assert missing_user.status_code == 404
    blank = client.get("/api/v1/queries/q-auth/export", headers={"X-User-Id": "   "})
    assert blank.status_code == 403
    unknown_query = export(client, "q-missing", "user-vip")
    assert unknown_query.status_code == 404

    allowed = export(client, "q-auth", "user-regular")
    assert allowed.status_code == 200
    assert allowed.headers["content-type"].startswith(ExportMedia.XLSX)


def test_header_columns_and_exact_strings(session, clock):
    add_user(session, "user-vip", clock, "vip")
    session.add(ProgramAlias(alias="海边故事别名", canonical_name="别名故事"))
    add_record(
        session,
        task_id="task-hit",
        record_id="rec-hit",
        program="海边故事",
        when=at_age(clock, 100),
        platform="示例视频",
        network="专网",
        hit_type="consensus",
    )
    add_record(
        session,
        task_id="task-alias",
        record_id="rec-alias",
        program="别名故事",
        when=at_age(clock, 120),
        platform="示例视频",
        network="公网",
        hit_type="consensus",
    )
    add_record(
        session,
        task_id="task-dispute",
        record_id="rec-dispute",
        program="别名故事",
        when=at_age(clock, 110),
        platform="示例视频",
        network="公网",
        hit_type="dispute",
    )
    add_record(
        session,
        task_id="task-manual",
        record_id="rec-manual",
        program="手工剧",
        when=at_age(clock, 140),
        hit_type="consensus",
    )
    add_query(
        session,
        clock,
        "q-layout",
        [
            {
                "uploaded": "海边故事",
                "matched": "海边故事",
                "resolution": "exact",
                "program_type": "电视剧",
            },
            {
                "uploaded": "海边故事别名",
                "matched": "别名故事",
                "resolution": "alias",
            },
            {
                "uploaded": "手工剧",
                "matched": "手工剧",
                "resolution": "manual",
                "program_type": "电影",
            },
        ],
    )
    session.commit()
    response = export(make_client(session, clock), "q-layout", "user-vip")
    assert response.status_code == 200
    disposition = response.headers["content-disposition"]
    assert quote(PartNames.single()) in disposition
    workbook = load_workbook(BytesIO(response.content))
    assert workbook.sheetnames == ["命中结果", "未命中与待处理"]
    sheet = workbook["命中结果"]
    assert sheet["A1"].value == "那豆在架 命中结果"
    block = meta_block(sheet)
    assert block["查询编号"] == "q-layout"
    assert block["查询范围"] == "3 个节目 · 已收录平台 · ≥30 天记录"
    assert block["生成日期"] == "2026-11-21"
    assert "部分" not in block
    headers, records, _styled = hit_table(sheet)
    assert headers == COLUMNS
    assert HitColumns.TYPE not in headers
    assert [row[1] for row in records] == ["电视剧", None, "电影"]
    assert [row[2] for row in records] == ["海边故事", "海边故事别名", "手工剧"]
    assert records[1][3] == "别名故事"
    joined = " ".join("" if value is None else str(value) for row in records for value in row)
    assert "客户" not in joined
    assert "%" not in joined
    assert "相似度" not in joined
    assert_no_match_labels(sheet)
    assert rest_table(workbook["未命中与待处理"]) == []
    workbook.close()


def test_alias_match_is_a_confirmed_hit_without_a_label(session, clock):
    add_user(session, "user-regular", clock, "regular")
    session.add(ProgramAlias(alias="标准故事的别名", canonical_name="标准故事"))
    add_record(
        session,
        task_id="task-alias",
        record_id="rec-alias",
        program="标准故事",
        when=at_age(clock, 100),
        hit_type="consensus",
    )
    add_record(
        session,
        task_id="task-exact",
        record_id="rec-exact",
        program="另一故事",
        when=at_age(clock, 110),
        hit_type="consensus",
    )
    add_query(
        session,
        clock,
        "q-alias",
        [
            {
                "uploaded": "标准故事的别名",
                "matched": "标准故事的别名",
                "resolution": "alias",
            },
            {
                "uploaded": "另一故事",
                "matched": "另一故事",
                "resolution": "exact",
            },
        ],
    )
    session.commit()
    response = export(make_client(session, clock), "q-alias", "user-regular")
    workbook = load_workbook(BytesIO(response.content))
    headers, records, _styled = hit_table(workbook["命中结果"])
    assert headers == COLUMNS
    assert [(row[2], row[3]) for row in records] == [
        ("标准故事的别名", "标准故事"),
        ("另一故事", "另一故事"),
    ]
    assert_no_match_labels(workbook["命中结果"])
    workbook.close()


def test_uniform_confirmed_label_drops_the_hit_type_column():
    confirmed = HitColumns.for_labels([HitColumns.CONFIRMED, HitColumns.CONFIRMED])
    assert confirmed == COLUMNS
    assert HitColumns.for_labels([]) == COLUMNS
    mixed = HitColumns.for_labels([HitColumns.CONFIRMED, "其他"])
    assert mixed[4] == HitColumns.TYPE
    assert mixed[:4] == COLUMNS[:4]
    assert mixed[-2:] == COLUMNS[-2:]


def test_two_records_of_the_same_platform_and_program(session, clock):
    add_user(session, "user-vip", clock, "vip")
    add_record(
        session,
        task_id="task-same-1",
        record_id="rec-same-1",
        program="重复节目",
        when=at_age(clock, 40, hour=9),
        platform="示例视频",
        network="专网",
        hit_type="consensus",
    )
    add_record(
        session,
        task_id="task-same-2",
        record_id="rec-same-2",
        program="重复节目",
        when=at_age(clock, 80, hour=15),
        platform="示例视频",
        network="专网",
        hit_type="consensus",
    )
    add_query(
        session,
        clock,
        "q-same",
        [{"uploaded": "重复节目", "matched": "重复节目", "resolution": "exact"}],
    )
    session.commit()
    response = export(make_client(session, clock), "q-same", "user-vip")
    workbook = load_workbook(BytesIO(response.content))
    _headers, records, _styled = hit_table(workbook["命中结果"])
    assert len(records) == 2
    assert records[0][0] == records[1][0] == "专网 · 示例视频"
    assert records[0][2] == records[1][2] == "重复节目"
    assert records[0][3] == records[1][3] == "重复节目"
    assert records[0][4] != records[1][4]
    images = embedded_images(response.content)
    assert len(images) == 2
    assert images[0] != images[1]
    workbook.close()


def test_excludes_test_prefix_and_non_done(session, clock):
    add_user(session, "user-vip", clock, "vip")
    when = at_age(clock, 100)
    add_record(
        session,
        task_id="task-keep",
        record_id="rec-keep",
        program="保留节目",
        when=when,
        filename="clip.mp4",
    )
    add_record(
        session,
        task_id="task-file",
        record_id="rec-file",
        program="保留节目",
        when=when,
        filename="Test_clip.mp4",
        is_test=False,
    )
    add_record(
        session,
        task_id="task-platform",
        record_id="rec-platform",
        program="保留节目",
        when=when,
        platform="TEST_台",
        is_test=False,
    )
    add_record(
        session,
        task_id="task-wide",
        record_id="rec-wide",
        program="保留节目",
        when=when,
        filename="ｔｅｓｔ＿wide.mp4",
        is_test=False,
    )
    add_record(
        session,
        task_id="task-flag",
        record_id="rec-flag",
        program="保留节目",
        when=when,
        is_test=True,
    )
    add_record(
        session,
        task_id="task-cancel",
        record_id="rec-cancel",
        program="保留节目",
        when=when,
        status="cancelled",
    )
    add_record(
        session,
        task_id="task-dup",
        record_id="rec-dup",
        program="保留节目",
        when=when,
        status="duplicate",
    )
    add_record(
        session,
        task_id="task-missing",
        record_id="rec-missing",
        program="保留节目",
        when=when,
        with_task=False,
    )
    add_record(
        session,
        task_id="task-young",
        record_id="rec-young",
        program="只有新记录",
        when=at_age(clock, 10),
    )
    add_query(
        session,
        clock,
        "q-filter",
        [
            {"uploaded": "保留节目", "matched": "保留节目", "resolution": "exact"},
            {"uploaded": "只有新记录", "matched": "只有新记录", "resolution": "exact"},
        ],
    )
    session.commit()
    response = export(make_client(session, clock), "q-filter", "user-vip")
    workbook = load_workbook(BytesIO(response.content))
    _headers, records, _styled = hit_table(workbook["命中结果"])
    assert len(records) == 2
    assert {row[0] for row in records} == {"专网 · 示例视频", PlatformNames.UNRECOGNIZED}
    assert rest_table(workbook["未命中与待处理"]) == [("只有新记录", RestStatus.MISS, None)]
    text = " ".join(str(value) for row in records for value in row if value)
    assert "TEST_台" not in text
    assert "Test_clip" not in text
    workbook.close()


def test_detection_date_is_beijing_calendar_day(session, clock):
    add_user(session, "user-regular", clock, "regular")
    # 16:30 UTC is 00:30 the next day in Asia/Shanghai.
    when = datetime(2026, 8, 20, 16, 30, tzinfo=UTC)
    add_record(
        session,
        task_id="task-date",
        record_id="rec-date",
        program="日期节目",
        when=when,
    )
    add_query(
        session,
        clock,
        "q-date",
        [{"uploaded": "日期节目", "matched": "日期节目", "resolution": "exact"}],
    )
    session.commit()
    response = export(make_client(session, clock), "q-date", "user-regular")
    workbook = load_workbook(BytesIO(response.content))
    _headers, records, _styled = hit_table(workbook["命中结果"])
    assert len(records) == 1
    assert records[0][4] == "2026-08-21"
    assert records[0][4] != "2026-08-20"
    workbook.close()


def test_screenshot_is_embedded_watermarked_and_has_no_hyperlink(session, clock):
    add_user(session, "user-vip", clock, "vip")
    add_record(
        session,
        task_id="task-blue",
        record_id="rec-blue",
        program="画面节目",
        when=at_age(clock, 120),
        hit_type="consensus",
    )
    add_record(
        session,
        task_id="task-green",
        record_id="rec-green",
        program="画面节目",
        when=at_age(clock, 110),
        hit_type="consensus",
    )
    add_record(
        session,
        task_id="task-open",
        record_id="rec-open",
        program="画面节目",
        when=at_age(clock, 130),
        hit_type="dispute",
    )
    add_record(
        session,
        task_id="task-empty",
        record_id="rec-empty",
        program="画面节目",
        when=at_age(clock, 100),
    )
    add_query(
        session,
        clock,
        "q-image",
        [
            {
                "uploaded": "画面节目",
                "matched": "画面节目",
                "resolution": "exact",
                "program_type": "综艺",
            }
        ],
    )
    session.commit()
    signer = RecordingSigner()
    reader = MappedFrameReader(
        {
            ("card", "rec-blue"): solid_png((20, 40, 180)),
            ("card", "rec-green"): solid_png((20, 140, 40)),
        }
    )
    frames = SignedDerivedFrameSource(signer, reader)
    response = export(
        make_client(session, clock, frames=frames, urls=signer),
        "q-image",
        "user-vip",
    )
    assert response.status_code == 200
    assert_no_hyperlinks(response.content)
    images = embedded_images(response.content)
    assert len(images) == 2
    for image in images:
        assert_watermark(image)
    assert images[0] != images[1]
    workbook = load_workbook(BytesIO(response.content))
    _headers, records, _styled = hit_table(workbook["命中结果"])
    assert len(records) == 3
    assert records[2][5] == "暂无截图"
    assert all(call[1] != "rec-open" for call in signer.calls)
    assert signer.calls
    assert {call[0] for call in signer.calls} == {"card"}
    assert all(call[2] == "user-vip" for call in signer.calls)
    raw = response.content.decode("latin-1", errors="ignore")
    assert all(url not in raw for url in signer.urls)
    workbook.close()


def test_sheet2_matches_the_unresolved_summary(session, clock):
    add_user(session, "user-regular", clock, "regular")
    add_record(
        session,
        task_id="task-seen",
        record_id="rec-seen",
        program="看得见",
        when=at_age(clock, 100),
    )
    add_record(
        session,
        task_id="task-too-new",
        record_id="rec-too-new",
        program="太新了",
        when=at_age(clock, 40),
    )
    add_query(
        session,
        clock,
        "q-rest",
        [
            {"uploaded": "看得见", "matched": "看得见", "resolution": "exact"},
            {"uploaded": "没有这场", "resolution": "miss"},
            {
                "uploaded": "待确认剧",
                "resolution": "pending",
                "detail": "候选甲、候选乙",
            },
            {
                "uploaded": "失败剧",
                "resolution": "failed",
                "detail": "名称无法解析",
            },
            {"uploaded": "太新了", "matched": "太新了", "resolution": "exact"},
        ],
    )
    session.commit()
    response = export(make_client(session, clock), "q-rest", "user-regular")
    workbook = load_workbook(BytesIO(response.content))
    _headers, records, _styled = hit_table(workbook["命中结果"])
    assert [row[2] for row in records] == ["看得见"]
    summary = rest_table(workbook["未命中与待处理"])
    assert summary == [
        ("没有这场", RestStatus.MISS, None),
        ("待确认剧", RestStatus.PENDING, "候选甲、候选乙"),
        ("失败剧", RestStatus.FAILED, "名称无法解析"),
        ("太新了", RestStatus.MISS, None),
    ]
    counts = {}
    for _name, status, _detail in summary:
        counts[status] = counts.get(status, 0) + 1
    assert counts == {RestStatus.MISS: 2, RestStatus.PENDING: 1, RestStatus.FAILED: 1}
    workbook.close()


def test_membership_age_floor_changes_rows_and_scope(session, clock):
    add_user(session, "user-vip", clock, "vip")
    add_user(session, "user-regular", clock, "regular")
    add_user(session, "user-both", clock, "regular")
    add_user(session, "user-both", clock, "vip")
    add_record(
        session,
        task_id="task-40",
        record_id="rec-40",
        program="跨档节目",
        when=at_age(clock, 40),
        hit_type="consensus",
    )
    add_record(
        session,
        task_id="task-100",
        record_id="rec-100",
        program="跨档节目",
        when=at_age(clock, 100),
        hit_type="consensus",
    )
    add_record(
        session,
        task_id="task-only-40",
        record_id="rec-only-40",
        program="仅四十天",
        when=at_age(clock, 40),
    )
    add_query(
        session,
        clock,
        "q-floor",
        [
            {"uploaded": "跨档节目", "matched": "跨档节目", "resolution": "exact"},
            {"uploaded": "仅四十天", "matched": "仅四十天", "resolution": "exact"},
        ],
    )
    session.commit()
    client = make_client(session, clock)

    vip = load_workbook(BytesIO(export(client, "q-floor", "user-vip").content))
    regular = load_workbook(BytesIO(export(client, "q-floor", "user-regular").content))
    both = load_workbook(BytesIO(export(client, "q-floor", "user-both").content))
    try:
        assert meta_block(vip["命中结果"])["查询范围"] == "2 个节目 · 已收录平台 · ≥30 天记录"
        assert meta_block(regular["命中结果"])["查询范围"] == "2 个节目 · 已收录平台 · ≥90 天记录"
        assert meta_block(both["命中结果"])["查询范围"] == "2 个节目 · 已收录平台 · ≥30 天记录"
        _headers, vip_rows, _styled = hit_table(vip["命中结果"])
        _headers, regular_rows, _styled = hit_table(regular["命中结果"])
        assert len(vip_rows) == 3
        assert [row[2] for row in regular_rows] == ["跨档节目"]
        assert rest_table(regular["未命中与待处理"]) == [("仅四十天", RestStatus.MISS, None)]
        assert rest_table(vip["未命中与待处理"]) == []
    finally:
        vip.close()
        regular.close()
        both.close()


def test_unrecognized_platform_is_still_a_hit(session, clock):
    add_user(session, "user-vip", clock, "vip")
    add_record(
        session,
        task_id="task-many",
        record_id="rec-many",
        program="多解节目",
        when=at_age(clock, 100),
        platform="双解平台",
        network="专网",
    )
    add_record(
        session,
        task_id="task-none",
        record_id="rec-none",
        program="无解节目",
        when=at_age(clock, 110),
        platform="没有这个平台",
        network="公网",
    )
    add_record(
        session,
        task_id="task-known",
        record_id="rec-known",
        program="已知节目",
        when=at_age(clock, 120),
        platform="示例视频ott",
        network="专网",
    )
    add_query(
        session,
        clock,
        "q-platform",
        [
            {"uploaded": "多解节目", "matched": "多解节目", "resolution": "exact"},
            {"uploaded": "无解节目", "matched": "无解节目", "resolution": "exact"},
            {"uploaded": "已知节目", "matched": "已知节目", "resolution": "exact"},
        ],
    )
    session.commit()
    response = export(make_client(session, clock), "q-platform", "user-vip")
    workbook = load_workbook(BytesIO(response.content))
    _headers, records, _styled = hit_table(workbook["命中结果"])
    by_name = {row[2]: row[0] for row in records}
    assert by_name == {
        "多解节目": PlatformNames.UNRECOGNIZED,
        "无解节目": PlatformNames.UNRECOGNIZED,
        "已知节目": "专网 · 示例视频",
    }
    assert len(records) == 3
    assert PlatformNames.UNRECOGNIZED == "平台未识别"
    assert rest_table(workbook["未命中与待处理"]) == []
    workbook.close()


def test_several_alias_targets_stay_one_title(session, clock):
    add_user(session, "user-vip", clock, "vip")
    session.add(ProgramAlias(alias="abc剧", canonical_name="节目1986"))
    session.add(ProgramAlias(alias="ａｂｃ剧", canonical_name="节目2010"))
    add_record(
        session,
        task_id="amb-1986",
        record_id="amb-rec-1986",
        program="节目1986",
        when=at_age(clock, 200),
        platform="示例视频",
    )
    add_record(
        session,
        task_id="amb-2010",
        record_id="amb-rec-2010",
        program="节目2010",
        when=at_age(clock, 40),
        platform="示例专网",
    )
    add_record(
        session,
        task_id="amb-title",
        record_id="amb-rec-title",
        program="abc剧",
        when=at_age(clock, 100),
        platform="示例视频",
    )
    add_query(
        session,
        clock,
        "q-same-name",
        [{"uploaded": "ABC剧", "matched": "节目1986", "resolution": "exact"}],
    )
    session.commit()
    response = export(make_client(session, clock), "q-same-name", "user-vip")
    workbook = load_workbook(BytesIO(response.content))
    _headers, records, _styled = hit_table(workbook["命中结果"])
    assert [row[3] for row in records] == ["abc剧"]
    assert rest_table(workbook["未命中与待处理"]) == []
    workbook.close()


def test_splits_when_the_workbook_would_exceed_the_limit(session, clock):
    add_user(session, "user-vip", clock, "vip")
    programs = []
    for index in range(3):
        name = f"拆分节目{index}"
        add_record(
            session,
            task_id=f"task-split-{index}",
            record_id=f"rec-split-{index}",
            program=name,
            when=at_age(clock, 100 + index),
        )
        programs.append({"uploaded": name, "matched": name, "resolution": "exact"})
    programs.append({"uploaded": "落在末卷", "resolution": "miss"})
    programs.append({"uploaded": "待核名", "resolution": "pending", "detail": "候选丙"})
    add_query(session, clock, "q-split", programs)
    session.commit()

    whole = export(make_client(session, clock), "q-split", "user-vip")
    assert whole.status_code == 200
    assert whole.headers["content-type"].startswith(ExportMedia.XLSX)
    assert quote(PartNames.single()) in whole.headers["content-disposition"]
    whole_book = load_workbook(BytesIO(whole.content))
    assert whole_book.sheetnames == ["命中结果", "未命中与待处理"]
    assert "部分" not in meta_block(whole_book["命中结果"])
    _headers, whole_rows, _styled = hit_table(whole_book["命中结果"])
    assert len(whole_rows) == 3
    whole_book.close()

    forced = export(make_client(session, clock, max_bytes=1024), "q-split", "user-vip")
    assert forced.headers["content-type"].startswith("application/zip")
    assert quote(PartNames.archive()) in forced.headers["content-disposition"]
    _assert_parts(forced.content, expected_rows=3)

    boundary = export(
        make_client(session, clock, max_bytes=len(whole.content) - 1),
        "q-split",
        "user-vip",
    )
    assert boundary.headers["content-type"].startswith("application/zip")
    _assert_parts(boundary.content, expected_rows=3)


def _assert_parts(blob: bytes, expected_rows: int):
    with zipfile.ZipFile(BytesIO(blob)) as archive:
        names = archive.namelist()
        assert names == [PartNames.part(index) for index in range(1, len(names) + 1)]
        assert len(names) >= 2
        payloads = [archive.read(name) for name in names]
    total_rows = 0
    for index, payload in enumerate(payloads, start=1):
        assert_no_hyperlinks(payload)
        workbook = load_workbook(BytesIO(payload))
        try:
            block = meta_block(workbook["命中结果"])
            assert workbook["命中结果"]["A1"].value == "那豆在架 命中结果"
            assert block["查询编号"] == "q-split"
            assert block["查询范围"] == "5 个节目 · 已收录平台 · ≥30 天记录"
            assert block["生成日期"] == "2026-11-21"
            assert block["部分"] == PartNames.label(index, len(payloads))
            _headers, rows, _styled = hit_table(workbook["命中结果"])
            total_rows += len(rows)
            assert len(rows) >= 1
            last = index == len(payloads)
            if last:
                assert workbook.sheetnames == ["命中结果", "未命中与待处理"]
                assert rest_table(workbook["未命中与待处理"]) == [
                    ("落在末卷", RestStatus.MISS, None),
                    ("待核名", RestStatus.PENDING, "候选丙"),
                ]
            else:
                assert workbook.sheetnames == ["命中结果"]
        finally:
            workbook.close()
    assert total_rows == expected_rows


def test_synthetic_frames_are_not_customer_images():
    first = SyntheticFrame.png("rec-a")
    second = SyntheticFrame.png("rec-b")
    assert first != second
    assert first.startswith(b"\x89PNG")
    prepared = FrameImage.prepare(first)
    assert prepared.width == 600
    assert FrameImage.MARK == "那豆在架"
    assert_watermark(prepared.jpeg)
