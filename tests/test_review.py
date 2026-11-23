import csv
import io
from datetime import timedelta

import pytest
from openpyxl import load_workbook
from sqlalchemy import select

from nadou.__main__ import NadouCommand
from nadou.config import get_settings
from nadou.hitlist import HitListImporter
from nadou.models import IndexRecord, ReviewProgramTitle, Task
from nadou.records import IndexRecordStore
from nadou.review import ReviewExport
from tests.test_hitlist import _workbook
from tests.test_query_export import add_query, add_user, export, make_client


@pytest.fixture
def review_titles(session):
    session.add_all(ReviewProgramTitle(program_name=name) for name in ("家庭", "远方", "狗"))
    session.flush()


def put(session, name, task="review-task", hit_type="consensus", when=None):
    return IndexRecordStore(session).upsert(
        task_id=task,
        program_name=name,
        program_name_raw=name,
        hit_type=hit_type,
        frame_count=3,
        timestamps=["00:01:02", "00:02:03"],
        uploaded_at=when,
    )


def test_import_and_incremental_writes_use_normalized_configuration(session, review_titles):
    session.add(ReviewProgramTitle(program_name="  ＡＢＣ  片  "))
    session.flush()
    content = _workbook(
        {
            "AI共识": [["节目名称", "命中帧数", "时间戳"]]
            + [
                [n, 2, "00:01:00"]
                for n in (" 家庭 ", "远方", "狗", "abc 片", "家庭电影", "父亲", "母亲")
            ]
        }
    )
    assert HitListImporter(session).import_xlsx(content, task_id="import") == 7
    session.flush()
    rows = {r.program_name: r for r in session.scalars(select(IndexRecord))}
    assert all(rows[name].hit_type == "review" for name in ("家庭", "远方", "狗", "abc 片"))
    assert all(rows[name].hit_type == "consensus" for name in ("家庭电影", "父亲", "母亲"))
    # Incremental writes take the same path, including updating existing rows.
    updated = put(session, " 家庭 ", "import")
    session.flush()
    assert updated.id == rows["家庭"].id and updated.hit_type == "review"
    assert updated.frame_count == 3
    assert put(session, "狗", "dispute", "dispute").hit_type == "dispute"
    assert put(session, "其他", "manual-review", "review").hit_type == "review"
    session.delete(session.get(ReviewProgramTitle, "家庭"))
    session.flush()
    assert put(session, "家庭", "new-task").hit_type == "consensus"


@pytest.mark.parametrize("title", ["家庭", "远方", "狗"])
@pytest.mark.parametrize("tier", [None, "regular", "vip"])
def test_review_only_is_a_miss_without_counts_prices_or_leaks(
    session, clock, review_titles, title, tier
):
    add_user(session, "review-user", clock, tier)
    for age in (10, 40, 100, 200):
        task = f"review-{age}"
        session.add(
            Task(
                task_id=task,
                status="done",
                created_at=clock - timedelta(days=age),
                platform_name="隐藏平台",
                platform_province="测试省",
                platform_operator=task,
                source_unverified=False,
            )
        )
        put(session, title, task)
    session.commit()
    client = make_client(session, clock)
    response = client.get(
        "/api/v1/programs/search", params={"name": title}, headers=client.auth("review-user")
    )
    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "miss"
    assert body["platform_count"] == 0
    assert body["platform_groups"] == body["locked_records"] == []
    summary = body["locked_summary"]
    assert summary["locked_total"] == summary["unlockable_total"] == 0
    assert not any(summary["by_required_tier"].values())
    assert not any(summary["unlocks_by_product"].values())
    assert summary["cheapest_full_unlock"] is None
    assert "隐藏平台" not in response.text


def test_mixed_review_is_excluded_from_summary_and_user_export(session, clock, review_titles):
    add_user(session, "review-vip", clock, "vip")
    hidden = put(session, "家庭", "hidden", when=clock - timedelta(days=100))
    visible = put(session, "家庭", "counted", when=clock - timedelta(days=100))
    # Represents an explicitly counted record; no review UI or action API is added.
    visible.hit_type = "consensus"
    visible.category = "已计入记录"
    hidden.category = "不应导出的字段"
    add_query(
        session,
        clock,
        "review-query",
        [{"uploaded": "家庭", "resolution": "exact"}],
        user_id="review-vip",
    )
    session.commit()
    client = make_client(session, clock)
    body = client.get(
        "/api/v1/programs/search", params={"name": "家庭"}, headers=client.auth("review-vip")
    ).json()
    ids = [r["record_id"] for g in body["platform_groups"] for r in g["records"]]
    assert ids == [visible.id]
    assert body["locked_summary"]["locked_total"] == 0
    result = export(client, "review-query", "review-vip")
    assert result.status_code == 200
    book = load_workbook(io.BytesIO(result.content))
    values = [cell.value for sheet in book for row in sheet for cell in row]
    assert "已计入记录" in values and "不应导出的字段" not in values
    # Pending-only titles also stay out of user hit rows.
    visible.hit_type = "review"
    session.commit()
    result = export(client, "review-query", "review-vip")
    book = load_workbook(io.BytesIO(result.content))
    values = [cell.value for sheet in book for row in sheet for cell in row]
    assert "已计入记录" not in values and "未命中" in values


def test_review_does_not_change_mixed_locked_prices(session, clock, review_titles):
    from nadou.search import ProgramSearch
    from nadou.signed_url import FakeSignedUrlProvider

    search = ProgramSearch(session, FakeSignedUrlProvider(), clock)
    visible = put(session, "家庭", "counted", when=clock - timedelta(days=100))
    visible.hit_type = "consensus"
    session.flush()
    before = search.search("家庭", None).model_dump()
    for age in (10, 40, 100, 200):
        put(session, "家庭", f"hidden-{age}", when=clock - timedelta(days=age))
    session.flush()
    assert search.search("家庭", None).model_dump() == before


def test_export_review_cli_is_read_only_and_only_exports_pending(
    session, clock, review_titles, monkeypatch, capsys
):
    record = put(session, "狗", when=clock)
    put(session, "父亲", "visible", when=clock)
    put(session, "远方", "dispute", "dispute", when=clock)
    session.add(Task(task_id="review-task", status="done", platform_name="测试平台"))
    session.commit()
    monkeypatch.setenv("DATABASE_URL", str(session.get_bind().url))
    get_settings.cache_clear()
    try:
        with pytest.raises(SystemExit) as exc:
            NadouCommand.main(["export-review"])
        assert exc.value.code == 0
    finally:
        get_settings.cache_clear()
    rows = list(csv.reader(io.StringIO(capsys.readouterr().out)))
    assert rows == [
        list(ReviewExport.COLUMNS),
        ["review-task", "狗", "测试平台", "3", '["00:01:02", "00:02:03"]'],
    ]
    session.expire_all()
    assert session.get(IndexRecord, record.id).hit_type == "review"
