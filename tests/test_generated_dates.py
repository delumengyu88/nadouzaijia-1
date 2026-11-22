import importlib.util
import json
from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path
from urllib.parse import quote

import pytest
from openpyxl import load_workbook
from sqlalchemy.exc import IntegrityError

from nadou.models import IndexRecord, Task
from nadou.platform_names import PlatformNames
from nadou.query_export import ExportCount, HitWorkbook, PartNames
from nadou.records import IndexRecordStore
from nadou.text import RecordAge
from tests.test_query_export import (
    add_query,
    add_record,
    add_user,
    export,
    hit_table,
    make_client,
)

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "backfill_generated_dates.py"


def load_backfill():
    spec = importlib.util.spec_from_file_location("backfill_generated_dates", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.GeneratedDates


def at_age(clock, days, hour=11):
    day = clock.astimezone(RecordAge.TZ).date() - timedelta(days=days)
    return datetime(day.year, day.month, day.day, hour, 0, 0, tzinfo=RecordAge.TZ)


def add_generated(session, *, record_id, program, when, task_id=None):
    """A row whose only date is a generated placeholder (orphan unless task_id exists)."""
    session.add(
        IndexRecord(
            id=record_id,
            task_id=task_id or f"orphan-{record_id}",
            program_name=program,
            program_name_raw=program,
            hit_type="consensus",
            frame_count=1,
            timestamps=["00:00:05"],
            uploaded_at=None,
            generated_uploaded_at=when,
            date_generated=True,
        )
    )


def search(client, name, user_id=None):
    headers = client.auth(user_id) if user_id else {}
    response = client.get("/api/v1/programs/search", params={"name": name}, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def open_rows(body):
    return {row["record_id"]: row for g in body["platform_groups"] for row in g["records"]}


def test_export_excludes_generated_date_records(session, clock):
    add_user(session, "user-vip", clock, "vip")
    add_record(
        session, task_id="t-real", record_id="rec-real", program="混合节目", when=at_age(clock, 100)
    )
    add_generated(session, record_id="rec-gen", program="混合节目", when=at_age(clock, 120))
    # Flagged row that later gained a task: still never exported.
    add_record(
        session,
        task_id="t-late",
        record_id="rec-late-dummy",
        program="占位节目",
        when=at_age(clock, 100),
        platform="山东移动",
    )
    add_generated(
        session, record_id="rec-late", program="混合节目", when=at_age(clock, 150), task_id="t-late"
    )
    add_generated(session, record_id="rec-only-gen", program="只有生成", when=at_age(clock, 150))
    add_query(
        session,
        clock,
        "q-gen",
        [
            {"uploaded": "混合节目", "matched": "混合节目", "resolution": "exact"},
            {"uploaded": "只有生成", "matched": "只有生成", "resolution": "exact"},
        ],
        user_id="user-vip",
    )
    session.commit()

    client = make_client(session, clock)
    body = search(client, "混合节目", "user-vip")
    assert {"rec-real", "rec-gen", "rec-late"} <= set(open_rows(body))

    response = export(client, "q-gen", "user-vip")
    assert response.status_code == 200
    book = load_workbook(BytesIO(response.content))
    sheet = book["命中结果"]
    _headers, rows, _styled = hit_table(sheet)
    assert len(rows) == 1
    assert rows[0][0] == "示例视频"
    dates = {row[6] for row in rows}
    assert at_age(clock, 120).strftime("%Y-%m-%d") not in dates
    assert at_age(clock, 150).strftime("%Y-%m-%d") not in dates
    rest = [r[0].value for r in book["未命中与待处理"].iter_rows(min_row=2) if r[0].value]
    assert rest == ["只有生成"]


def test_export_count_in_first_row_and_filename_matches_rows(session, clock):
    add_user(session, "user-vip", clock, "vip")
    for index in range(3):
        add_record(
            session,
            task_id=f"t-c{index}",
            record_id=f"rec-c{index}",
            program="计数节目",
            when=at_age(clock, 40 + index),
        )
    add_generated(session, record_id="rec-c-gen", program="计数节目", when=at_age(clock, 70))
    add_query(
        session,
        clock,
        "q-count",
        [{"uploaded": "计数节目", "matched": "计数节目", "resolution": "exact"}],
        user_id="user-vip",
    )
    session.commit()
    client = make_client(session, clock)
    body = search(client, "计数节目", "user-vip")
    assert len(open_rows(body)) == 4  # P08 shows four; only three may be exported.

    response = export(client, "q-count", "user-vip")
    sheet = load_workbook(BytesIO(response.content))["命中结果"]
    _headers, rows, _styled = hit_table(sheet)
    assert len(rows) == 3
    assert sheet["A1"].value == HitWorkbook.first_row(len(rows))
    assert sheet["A1"].value.endswith(f"（{ExportCount.label(len(rows))}）")
    assert ExportCount.label(3) == "共导出 3 条"
    disposition = response.headers["content-disposition"]
    assert quote(PartNames.single(len(rows))) in disposition
    assert PartNames.single(3) == "汇总-共导出3条.xlsx"


def test_evidence_prefill_excludes_generated_date_records(session, client, clock):
    add_user(session, "user-vip", clock, "vip")
    add_record(
        session,
        task_id="t-ev",
        record_id="rec-ev",
        program="取证节目",
        when=at_age(clock, 100),
        platform="示例视频",
    )
    session.add(
        Task(
            task_id="t-ev-late",
            status="done",
            created_at=at_age(clock, 95),
            raw_filename="x.mp4",
            platform_name="山东移动",
            platform_province="山东",
            platform_operator="移动",
            source_unverified=False,
            is_test=False,
        )
    )
    add_generated(
        session,
        record_id="rec-ev-late",
        program="取证节目",
        when=at_age(clock, 150),
        task_id="t-ev-late",
    )
    add_generated(session, record_id="rec-ev-orphan", program="取证节目", when=at_age(clock, 160))
    add_generated(session, record_id="rec-only", program="仅生成节目", when=at_age(clock, 160))
    session.commit()

    body = search(client, "取证节目", "user-vip")
    rows = open_rows(body)
    assert body["evidence_platforms"] == ["示例视频"]
    assert body["evidence_program_name"] == "取证节目"
    assert rows["rec-ev"]["can_download_report"] is True
    assert rows["rec-ev-late"]["can_download_report"] is False
    assert rows["rec-ev-orphan"]["can_download_report"] is False
    only = search(client, "仅生成节目", "user-vip")
    assert only["evidence_platforms"] == []
    assert only["evidence_program_name"] == ""
    assert all(not r["can_download_report"] for r in open_rows(only).values())


@pytest.mark.parametrize(
    ("age", "user", "visible"),
    [
        (29, "user-vip", False),
        (30, "user-vip", True),
        (89, "user-regular", False),
        (90, "user-regular", True),
        (179, None, False),
        (180, None, True),
    ],
)
def test_generated_date_sets_search_tier(session, client, clock, age, user, visible):
    add_user(session, "user-vip", clock, "vip")
    add_user(session, "user-regular", clock, "regular")
    add_generated(session, record_id="rec-tier", program="分档节目", when=at_age(clock, age))
    session.commit()
    body = search(client, "分档节目", user)
    rows = open_rows(body)
    if visible:
        row = rows["rec-tier"]
        assert row["checked_at"].startswith(at_age(clock, age).strftime("%Y-%m-%d"))
        assert row["platform"] == PlatformNames.OTHER
    else:
        assert "rec-tier" not in rows
        locked = {r["record_id"]: r for r in body["locked_records"]}
        assert locked["rec-tier"]["age_bucket"] != "unknown"
    raw = json.dumps(body, ensure_ascii=False)
    for word in ("生成", "估算", "待核实", "date_generated", "generated"):
        assert word not in raw


def test_other_platform_sorts_last(session, client, clock):
    add_user(session, "user-vip", clock, "vip")
    add_generated(session, record_id="rec-o-gen", program="排序节目", when=at_age(clock, 60))
    add_record(
        session,
        task_id="t-o-unk",
        record_id="rec-o-unk",
        program="排序节目",
        when=at_age(clock, 50),
        unverified=True,
    )
    add_record(
        session,
        task_id="t-o-a",
        record_id="rec-o-a",
        program="排序节目",
        when=at_age(clock, 200),
        platform="示例视频",
    )
    add_query(
        session,
        clock,
        "q-order",
        [{"uploaded": "排序节目", "matched": "排序节目", "resolution": "exact"}],
        user_id="user-vip",
    )
    session.commit()
    body = search(client, "排序节目", "user-vip")
    groups = body["platform_groups"]
    assert [g["platform"] for g in groups] == ["示例视频", PlatformNames.OTHER]
    # Real dates before generated ones inside 其他平台.
    assert [r["record_id"] for r in groups[-1]["records"]] == ["rec-o-unk", "rec-o-gen"]

    response = export(make_client(session, clock), "q-order", "user-vip")
    _headers, rows, _styled = hit_table(load_workbook(BytesIO(response.content))["命中结果"])
    assert [row[0] for row in rows] == ["示例视频", PlatformNames.OTHER]


def test_generated_flag_requires_null_uploaded_at(session, clock):
    session.add(
        IndexRecord(
            id="bad",
            task_id="x",
            program_name="坏",
            program_name_raw="坏",
            hit_type="consensus",
            frame_count=1,
            timestamps=[],
            uploaded_at=at_age(clock, 70),
            generated_uploaded_at=at_age(clock, 70),
            date_generated=True,
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_real_upload_time_replaces_generated_date(session, clock):
    add_generated(
        session, record_id="rec-up", program="补日期", when=at_age(clock, 70), task_id="t-up"
    )
    session.commit()
    row = IndexRecordStore(session).upsert(
        task_id="t-up",
        program_name="补日期",
        program_name_raw="补日期",
        hit_type="consensus",
        frame_count=1,
        timestamps=[],
        uploaded_at=at_age(clock, 40),
    )
    session.commit()
    assert row.uploaded_at is not None
    assert row.date_generated is False and row.generated_uploaded_at is None


def test_backfill_is_deterministic_bounded_and_reversible(session, clock):
    backfill = load_backfill()
    add_record(
        session, task_id="t-keep", record_id="rec-keep", program="回填节目", when=at_age(clock, 40)
    )
    for index in range(50):
        session.add(
            IndexRecord(
                id=f"orph-{index}",
                task_id=f"missing-{index}",
                program_name="回填节目",
                program_name_raw="回填节目",
                hit_type="consensus",
                frame_count=1,
                timestamps=[],
            )
        )
    session.commit()

    assert backfill.backfill(session, dry_run=True) == 50
    session.rollback()
    assert session.query(IndexRecord).filter_by(date_generated=True).count() == 0

    assert backfill.backfill(session, dry_run=False) == 50
    session.commit()
    first = {r.id: r.generated_uploaded_at for r in session.query(IndexRecord).all()}
    assert first["rec-keep"] is None
    base = date(2026, 11, 22)
    for record_id, when in first.items():
        if record_id == "rec-keep":
            continue
        days = (base - when.astimezone(RecordAge.TZ).date()).days
        assert 60 <= days <= 190
        assert when == backfill.generated_time(record_id)
    assert session.query(IndexRecord).filter(IndexRecord.uploaded_at.isnot(None)).count() == 0
    assert backfill.backfill(session, dry_run=False) == 0  # idempotent

    assert backfill.backfill(session, dry_run=False, undo=True) == 50
    session.commit()
    assert session.query(IndexRecord).filter_by(date_generated=True).count() == 0
    assert (
        session.query(IndexRecord).filter(IndexRecord.generated_uploaded_at.isnot(None)).count()
        == 0
    )
    assert backfill.backfill(session, dry_run=False) == 50
    session.commit()
    again = {r.id: r.generated_uploaded_at for r in session.query(IndexRecord).all()}
    assert again == first


@pytest.mark.parametrize("import_without_date_first", [False, True])
def test_backfill_then_task_import_restores_export_and_evidence(
    session, client, clock, import_without_date_first
):
    from nadou.importer import TaskExportImporter

    add_user(session, "user-vip", clock, "vip")
    add_record(
        session,
        task_id="recovered-task",
        record_id="recovered-record",
        program="恢复日期节目",
        when=None,
        with_task=False,
    )
    add_query(
        session,
        clock,
        "q-recovered",
        [
            {"uploaded": "恢复日期节目", "matched": "恢复日期节目", "resolution": "exact"},
        ],
        user_id="user-vip",
    )
    session.commit()
    generated = load_backfill()
    assert generated.backfill(session, dry_run=False) == 1
    session.commit()
    record = session.get(IndexRecord, "recovered-record")
    assert record.date_generated and record.generated_uploaded_at is not None
    original_generated = record.generated_uploaded_at
    assert search(client, "恢复日期节目", "user-vip")["evidence_platforms"] == []
    before = export(make_client(session, clock), "q-recovered", "user-vip")
    assert hit_table(load_workbook(BytesIO(before.content))["命中结果"])[1] == []

    header = "task_id,状态,提交时间,filename,公网/专网,平台名\n"
    importer = TaskExportImporter(session)
    if import_without_date_first:
        importer.import_csv(header + "recovered-task,done,,恢复.mp4,公网,山东移动\n")
        session.commit()
        session.refresh(record)
        assert record.date_generated and record.generated_uploaded_at == original_generated

    real_time = at_age(clock, 100)
    imported = importer.import_csv(
        header + f"recovered-task,done,{real_time.strftime('%Y-%m-%d %H:%M:%S')},"
        "恢复.mp4,公网,山东移动\n"
    )
    assert imported.updated == int(import_without_date_first)
    session.commit()
    session.refresh(record)
    assert not record.date_generated
    assert record.generated_uploaded_at is None
    assert record.uploaded_at is None  # Real time belongs to the recovered task.

    body = search(client, "恢复日期节目", "user-vip")
    assert body["evidence_platforms"] == ["山东移动"]
    assert body["evidence_program_name"] == "恢复日期节目"
    row = open_rows(body)["recovered-record"]
    assert row["can_download_report"] is True
    assert row["checked_at"].startswith(real_time.strftime("%Y-%m-%d"))
    after = export(make_client(session, clock), "q-recovered", "user-vip")
    rows = hit_table(load_workbook(BytesIO(after.content))["命中结果"])[1]
    assert len(rows) == 1
    assert rows[0][0] == "山东移动"
    assert rows[0][6] == real_time.strftime("%Y-%m-%d")
