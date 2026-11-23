import csv
import sqlite3
from datetime import timedelta
from io import BytesIO

import pytest
from openpyxl import load_workbook
from sqlalchemy import select

from nadou.config import get_settings
from nadou.models import HomepageExample, IndexRecord, Task
from nadou.query_export import HitWorkbook
from nadou.summary_source import SummaryPermission, approved_summary, file_hash, publish
from tests.test_query_export import add_query, add_record, add_user, embedded_images, make_client


def source(session, clock, tmp_path, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    get_settings.cache_clear()
    task = "synthetic-one"
    title = "合成节目"
    date = (clock - timedelta(days=70)).replace(microsecond=0)
    name = "专网-广东移动-合成节目.mp4"
    for tid, rid in [(task, "r-one"), ("another-task", "other-record")]:
        add_record(
            session,
            task_id=tid,
            record_id=rid,
            program=title,
            when=date,
            platform="广东移动",
            filename=name,
        )
        t = session.get(Task, tid)
        t.platform_name = "广东移动"
        t.platform_operator = "移动"
        t.platform_province = "广东"
    r = session.get(IndexRecord, "r-one")
    r.frame_count = 3
    r.timestamps = ["00:00:03,00:00:07,00:00:11"]
    session.add(
        IndexRecord(
            task_id=task,
            id="r-two",
            program_name="另一部",
            program_name_raw="另一部",
            hit_type="consensus",
            frame_count=1,
            timestamps=["00:00:08"],
        )
    )
    session.add(HomepageExample(id="first", program_name=title, sort_order=1, online=True))
    for uid, kind in [
        ("free", None),
        ("regular", "regular"),
        ("vip", "vip"),
        ("picture", "screenshot"),
        ("program", "program_30"),
    ]:
        add_user(
            session,
            uid,
            clock,
            kind=kind,
            program=title if uid == "program" else None,
            record_id="r-one" if uid == "picture" else None,
        )
    add_query(session, clock, "one", [{"uploaded": title, "resolution": "exact"}], "vip")
    add_query(
        session,
        clock,
        "mix",
        [{"uploaded": title, "resolution": "exact"}, {"uploaded": "另一部", "resolution": "miss"}],
        "vip",
    )
    session.commit()
    w2 = tmp_path / "w2.db"
    with sqlite3.connect(w2) as db:
        db.execute(
            "CREATE TABLE program_index(task_id,program_name,frames TEXT,timestamps,"
            "created_at,filename,status,hit_type)"
        )
        db.execute(
            "INSERT INTO program_index VALUES (?,?,?,?,?,?,?,?)",
            (
                task,
                title,
                3,
                ",".join(r.timestamps),
                date.replace(tzinfo=None).isoformat(),
                name,
                "done",
                "consensus",
            ),
        )
    tasks = tmp_path / "tasks.csv"
    with tasks.open("w") as f:
        csv.writer(f).writerows(
            [
                ["task_id", "状态", "提交时间", "filename", "公网/专网", "平台名"],
                [task, "done", date.strftime("%Y-%m-%d %H:%M:%S"), name, "专网", "广东移动"],
            ]
        )
    feedback = tmp_path / "feedback"
    feedback.write_bytes(b"synthetic approval")
    permission = SummaryPermission(
        scope="single_program_summary",
        review="pass",
        rights_holder="fixture",
        task_id=task,
        program=title,
        platform="广东移动",
        frame_count=3,
        w2_sha256=file_hash(w2),
        tasks_sha256=file_hash(tasks),
        feedback_sha256=file_hash(feedback),
    )
    return permission, w2, tasks, file_hash(feedback)


def test_summary_never_unlocks_assets_or_another_row(session, clock, tmp_path, monkeypatch):
    p, w, cs, feedback = source(session, clock, tmp_path, monkeypatch)
    for _ in range(2):
        publish(session, p, w, cs, feedback)
        session.commit()
    updated = session.scalars(
        select(IndexRecord).where(IndexRecord.summary_grant.is_not(None))
    ).all()
    assert [r.id for r in updated] == ["r-one"]
    task = session.get(Task, p.task_id)
    assert task.approved_hitlist_sha256 is None and updated[0].hitlist_sha256 is None
    client = make_client(session, clock)
    end = "/api/v1/programs/search"
    for user in ("free", "regular", "picture", "vip", "program"):
        resp = client.get(end, params={"name": p.program}, headers=client.auth(user))
        assert resp.status_code == 200
        body = resp.json()
        assert (
            body["outcome"] == "hit"
            and body["summary_only_count"] == 1
            and body["platform_count"] == 1
        )
        assert not body["evidence_program_name"] and not body["evidence_platforms"]
        assert not body["locked_records"] and body["locked_summary"]["unlockable_total"] == 0
        if user not in ("vip", "program"):
            assert body["summary_hidden_count"] == 1 and body["platform_groups"] == []
        else:
            assert body["summary_hidden_count"] == 0 and len(body["platform_groups"]) == 1
            rows = body["platform_groups"][0]["records"]
            assert len(rows) == 1
            assert set(rows[0]) == {
                "record_id",
                "age_bucket",
                "locked",
                "access",
                "summary_only",
                "can_download_report",
                "can_download_screenshot",
                "platform",
                "network_type",
                "hit_label",
                "checked_on",
                "frame_count",
            }
            assert (
                rows[0]["summary_only"]
                and rows[0]["access"] == "summary"
                and rows[0]["frame_count"] == 3
            )
            assert not rows[0]["can_download_report"] and not rows[0]["can_download_screenshot"]
            assert rows[0]["checked_on"] == (clock - timedelta(days=70)).date().isoformat()
    client.headers.update(client.auth("vip"))
    assert client.get(end, params={"name": "另一部"}).json()["outcome"] == "unverified"
    assert client.get("/api/v1/app/examples/" + p.program).status_code == 404
    assert client.get("/api/v1/app/home").json()["examples"] == []
    item = client.get("/api/v1/queries/one").json()["items"][0]
    assert item["hit"] and "命中 1 条" in item["line"] and not item["evidenceName"]
    for suffix in ("/export", "/program-export"):
        assert (
            client.get(
                "/api/v1/queries/one" + suffix, params={"program_name": p.program}
            ).status_code
            == 403
        )
    mixed = client.get("/api/v1/queries/mix/export")
    assert mixed.status_code == 200 and not embedded_images(mixed.content)
    book = load_workbook(BytesIO(mixed.content), read_only=True)
    try:
        cells = {c for row in book[HitWorkbook.REST].values for c in row}
        assert "仅限在线摘要" in cells and p.platform not in cells
    finally:
        book.close()
    # A changed row cannot retain display permission or fall through to generic output.
    saved = dict(updated[0].summary_grant)
    for change in ({"review": "withdrawn"}, {"feedback_sha256": None}, {"w2_sha256": "no"}):
        updated[0].summary_grant = {**saved, **change}
        session.commit()
        assert client.get(end, params={"name": p.program}).json()["outcome"] == "unverified"
    updated[0].summary_grant = saved
    updated[0].timestamps = ["00:05"]
    session.commit()
    assert not approved_summary(updated[0], task)
    assert client.get(end, params={"name": p.program}).json()["outcome"] == "unverified"
    assert client.get("/api/v1/queries/one").json()["items"][0]["unverified"]
    updated[0].summary_grant = None
    session.commit()
    assert client.get("/api/v1/app/examples/" + p.program).status_code == 404


def test_summary_import_checks_source_feedback_and_all_frames_before_commit(
    session, clock, tmp_path, monkeypatch
):
    p, w, cs, feedback = source(session, clock, tmp_path, monkeypatch)
    for key, value in [
        ("frame_count", 2),
        ("platform", "广东联通"),
        ("program", "不匹配"),
        ("task_id", "another-task"),
        ("w2_sha256", "a" * 64),
        ("tasks_sha256", "b" * 64),
    ]:
        with pytest.raises(ValueError):
            publish(session, p.model_copy(update={key: value}), w, cs, feedback)
        session.rollback()
    with pytest.raises(ValueError):
        publish(session, p, w, cs, "f" * 64)
    row = session.get(IndexRecord, "r-one")
    row.timestamps = ["00:00:03,00:00:07,00:00:12"]
    session.commit()
    with pytest.raises(ValueError):
        publish(session, p, w, cs, feedback)
    assert row.summary_grant is None
