import io
from datetime import datetime

from openpyxl import Workbook
from sqlalchemy import select

from nadou.hitlist import HitListImporter
from nadou.models import IndexRecord, Task
from nadou.search import ProgramSearch
from nadou.signed_url import FakeSignedUrlProvider
from nadou.text import RecordAge


def _workbook(sheets: dict[str, list[list]]) -> bytes:
    book = Workbook()
    first = True
    for title, rows in sheets.items():
        sheet = book.active if first else book.create_sheet(title)
        sheet.title = title
        first = False
        for row in rows:
            sheet.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def test_only_consensus_sheets_are_indexed(session):
    content = _workbook(
        {
            "AI分歧": [["节目名称", "命中帧数", "时间戳"], ["分歧节目", 3, "00:03:00"]],
            "非共识节目": [
                ["节目名称", "命中帧数", "时间戳", "类别"],
                ["非共识节目名", 1, "00:01:00", "电视剧"],
            ],
            "AI共识": [["节目名称", "命中帧数", "时间戳"], ["共识节目", 2, "00:02:00"]],
            "双引擎共识节目": [
                ["节目名称", "命中帧数", "时间戳", "类别"],
                ["旧共识节目", 4, "00:04:00", "电影"],
            ],
        }
    )
    written = HitListImporter(session).import_xlsx(content, task_id="hit00001")
    session.flush()
    assert written == 2
    rows = list(session.scalars(select(IndexRecord).order_by(IndexRecord.program_name)).all())
    assert [row.program_name for row in rows] == ["共识节目", "旧共识节目"]
    assert all(row.hit_type == "consensus" for row in rows)
    by_name = {row.program_name: row for row in rows}
    assert by_name["共识节目"].category is None
    assert by_name["旧共识节目"].category == "电影"
    assert by_name["旧共识节目"].frame_count == 4
    assert by_name["旧共识节目"].timestamps == ["00:04:00"]
    session.add(
        Task(
            task_id="hit00001",
            status="done",
            created_at=datetime(2020, 1, 1, 8, 0, tzinfo=RecordAge.TZ),
            raw_filename="hit00001.mp4",
            network_type="公网",
            platform_name="广东移动",
            platform_province="广东",
            platform_operator="移动",
            platform_subtype="OTT",
            source_unverified=False,
            is_test=False,
        )
    )
    session.flush()
    found = ProgramSearch(
        session,
        FakeSignedUrlProvider(now=lambda: 1),
        datetime(2026, 11, 21, 12, 0, tzinfo=RecordAge.TZ),
    ).search("旧共识节目", None)
    visible = found.platform_groups[0].records[0]
    assert found.platform_groups[0].platform == "广东移动"
    assert visible.category == "电影"
    assert visible.platform_subtype == "OTT"
    fresh = ProgramSearch(
        session,
        FakeSignedUrlProvider(now=lambda: 1),
        datetime(2026, 11, 21, 12, 0, tzinfo=RecordAge.TZ),
    ).search("共识节目", None)
    assert fresh.platform_groups[0].records[0].category is None
