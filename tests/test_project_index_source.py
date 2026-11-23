import hashlib
from datetime import timedelta
from io import BytesIO

import pytest
from openpyxl import load_workbook
from sqlalchemy import select

from nadou.config import get_settings
from nadou.hitlist import HitListImporter, import_approved
from nadou.models import HomepageExample, IndexRecord, Task
from tests.test_hitlist import _workbook
from tests.test_query_export import add_query, add_user, embedded_images, make_client


def workbook(title):
    return _workbook({"AI共识": [["节目名称", "命中帧数"], [title, 3]]})


def sheet_values(response):
    assert response.status_code == 200
    assert not embedded_images(response.content)
    book = load_workbook(BytesIO(response.content), read_only=True)
    try:
        return {value for sheet in book for row in sheet.values for value in row if value}
    finally:
        book.close()


def test_production_search_history_example_and_download_share_approval(session, clock, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    get_settings.cache_clear()
    title = "专用片单测试节目"
    task = Task(
        task_id="source-task",
        status="done",
        created_at=clock - timedelta(days=200),
        raw_filename="fixture.mp4",
        is_test=False,
        platform_name="广东移动",
        platform_operator="移动",
        platform_province="广东",
        source_unverified=False,
    )
    session.add(task)
    add_user(session, "member", clock, kind="vip")
    add_user(session, "other", clock)
    session.flush()
    session.add(HomepageExample(id="example", program_name=title, sort_order=0, online=True))
    for old in ("exact", "miss"):
        add_query(
            session,
            clock,
            old,
            [{"uploaded": title, "resolution": old, "detail": "旧命中信息"}],
            "member",
        )
    session.commit()
    client = make_client(session, clock)
    client.headers.update(client.auth("member"))
    importer = HitListImporter(session)
    content = workbook(title)
    digest = hashlib.sha256(content).hexdigest()
    assert importer.import_xlsx(content, task_id=task.task_id) == 1
    session.commit()
    search = "/api/v1/programs/search"
    example = "/api/v1/app/examples/" + title

    def unavailable():
        result = client.get(search, params={"name": title}).json()
        assert (
            result["outcome"] == "unverified"
            and result["platform_groups"] == []
            and result["locked_records"] == []
        )
        assert (
            not result["evidence_program_name"]
            and result["locked_summary"]["unlockable_total"] == 0
        )
        assert client.get("/api/v1/app/home").json()["platformTotal"] == 0
        assert client.get(example).status_code == 404
        for row in client.get("/api/v1/queries").json()["items"]:
            assert (
                row["badge"] == "待核验"
                and "数据待核验" in row["line"]
                and "旧命中" not in row["line"]
            )
        for old in ("exact", "miss"):
            record = client.get("/api/v1/queries/" + old).json()
            assert "待核验" in record["head"] and record["items"][0]["unverified"]
            assert not record["items"][0]["hit"] and not record["items"][0]["missed"]
            assert not record["items"][0].get("evidenceName")
            cells = sheet_values(client.get("/api/v1/queries/" + old + "/export"))
            assert "数据待核验" in cells and "旧命中信息" not in cells and "未命中" not in cells

    unavailable()
    with pytest.raises(ValueError, match="不匹配"):
        importer.import_xlsx(content, task_id=task.task_id, approved_sha256="0" * 64)
    importer.import_xlsx(content, task_id=task.task_id, approved_sha256=digest)
    session.commit()
    with pytest.raises(ValueError, match="授权清单"):
        importer.import_xlsx(workbook("另一个节目"), task_id=task.task_id)
    report = client.get(search, params={"name": title}).json()
    assert report["outcome"] == "hit" and report["platform_count"] == 1
    row = report["platform_groups"][0]["records"][0]
    assert row["can_download_report"] and not row["can_download_screenshot"]
    assert not row.get("thumbnail_url") and not row.get("hd_url") and "signed://" not in str(report)
    assert client.get("/api/v1/app/home").json()["platformTotal"] == 1
    assert client.get(example).status_code == 200
    for old in ("exact", "miss"):
        assert client.get("/api/v1/queries/" + old).json()["items"][0]["hit"]
        cells = sheet_values(client.get("/api/v1/queries/" + old + "/export"))
        assert "暂无截图" in cells and title in cells
    assert client.get("/api/v1/queries/exact/export", headers=client.auth("other")).status_code in (
        403,
        404,
    )
    assert client.post("/api/v1/queries/exact/results", json={"name": title}).json()["items"][0][
        "hit"
    ]
    task.raw_filename = "test_fixture.mp4"
    session.commit()
    unavailable()
    task.raw_filename = "fixture.mp4"
    row = session.scalar(select(IndexRecord).where(IndexRecord.task_id == task.task_id))
    row.generated_uploaded_at = clock
    row.date_generated = True
    session.commit()
    unavailable()
    row.generated_uploaded_at = None
    row.date_generated = False
    row.hit_type = "review"
    session.commit()
    unavailable()
    row.hit_type = "consensus"
    new = workbook("新批次节目")
    importer.import_xlsx(new, task_id=task.task_id, approved_sha256=hashlib.sha256(new).hexdigest())
    session.commit()
    assert client.get(search, params={"name": title}).json()["outcome"] == "unverified"
    assert client.get(search, params={"name": "新批次节目"}).json()["outcome"] == "hit"


def test_program_export_selects_only_owned_requested_item(session, clock):
    for uid in ("owner", "other"):
        add_user(session, uid, clock, kind="vip")
    add_query(
        session,
        clock,
        "batch",
        [
            {"uploaded": "第一个节目", "resolution": "miss"},
            {"uploaded": "第二个节目", "resolution": "pending"},
        ],
        "owner",
    )
    session.commit()
    client = make_client(session, clock)
    client.headers.update(client.auth("owner"))
    base = "/api/v1/queries/batch"
    for path in ("/program-export", "/export"):
        response = client.get(base + path, params={"program_name": "第一个节目"})
        assert response.headers["X-Nadou-Export-Scope"] == "program"
        cells = sheet_values(response)
        assert "第一个节目" in cells and "第二个节目" not in cells
    assert client.get(base + "/program-export").status_code == 422
    assert (
        client.get(base + "/program-export", params={"program_name": "不存在"}).status_code == 404
    )
    assert (
        client.get(
            base + "/program-export",
            params={"program_name": "第一个节目"},
            headers=client.auth("other"),
        ).status_code
        == 404
    )
    response = client.get(base + "/export")
    assert "X-Nadou-Export-Scope" not in response.headers
    assert {"第一个节目", "第二个节目"}.issubset(sheet_values(response))


def test_manifest_requires_unique_task_and_approved_version(session, clock, monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", str(session.get_bind().url))
    get_settings.cache_clear()
    content = workbook("授权片单")
    sha = hashlib.sha256(content).hexdigest()
    task = Task(
        task_id="listed",
        status="done",
        created_at=clock,
        raw_filename="fixture.mp4",
        is_test=False,
        source_unverified=True,
    )
    session.add(task)
    session.commit()
    manifest = tmp_path / "manifest.csv"
    book = tmp_path / "book.xlsx"
    book.write_bytes(content)
    args = ["--manifest", str(manifest), "--workbook", str(book), "--task-id", "listed"]
    for rows, message in (
        ([f"other,{sha}"], "唯一授权"),
        ([f"listed,{sha}", f"listed,{sha}"], "唯一授权"),
        (["listed," + "f" * 64], "不匹配"),
    ):
        manifest.write_text("task_id,sha256\n" + "\n".join(rows), encoding="utf-8")
        with pytest.raises(ValueError, match=message):
            import_approved(args)
        session.refresh(task)
        assert task.approved_hitlist_sha256 is None
    manifest.write_text("task_id,sha256\nlisted," + sha, encoding="utf-8")
    assert import_approved(args) == 0
    session.refresh(task)
    assert task.approved_hitlist_sha256 == sha
