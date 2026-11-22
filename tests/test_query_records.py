"""Owner-only query history. Search itself does not write these rows."""

from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select

from nadou.models import IndexRecord, MemberQuery, QueryProgram, Task
from nadou.query_records import ResultSummary
from nadou.text import RecordAge
from tests.test_generated_dates import add_generated
from tests.test_query_export import add_query, add_record, add_user, at_age, export, make_client


def listed(client, user_id, **params):
    response = client.get("/api/v1/queries", params=params, headers=client.auth(user_id))
    assert response.status_code == 200, response.text
    return response.json()


def create(client, user_id, payload):
    response = client.post("/api/v1/queries", json=payload, headers=client.auth(user_id))
    return response


def record(client, user_id, query_id, payload):
    return client.post(
        f"/api/v1/queries/{query_id}/results",
        json=payload,
        headers=client.auth(user_id),
    )


def test_routes_require_the_same_session_as_me(session, clock):
    client = make_client(session, clock)
    assert client.get("/api/v1/queries").status_code == 401
    assert client.post("/api/v1/queries", json={"name": "海边故事"}).status_code == 401
    assert client.get("/api/v1/queries/missing").status_code == 401
    assert (
        client.post("/api/v1/queries/missing/results", json={"name": "海边故事"}).status_code == 401
    )
    assert client.post("/api/v1/queries/missing/seen").status_code == 401


def test_create_accepts_a_list_or_one_name_and_only_the_owner_can_read(session, clock):
    add_user(session, "owner", clock)
    add_user(session, "other", clock)
    session.commit()
    client = make_client(session, clock)

    created = create(client, "owner", {"names": ["  海边故事  ", "海边故事", "雾港来信"]})
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["queued"] is True
    assert body["done"] is False
    assert body["seen_at"] is None
    assert body["networkError"] is None
    assert body["showNetworkRetry"] is None
    assert body["failBanner"] is None
    assert [item["name"] for item in body["items"]] == ["海边故事", "雾港来信"]
    assert body["items"][0]["badge"] == "查询中"
    assert body["items"][0]["badgeCls"] == "bad"
    assert body["items"][0]["evidenceName"] is None
    assert body["items"][0]["evidencePlatform"] is None

    single = create(client, "owner", {"name": "单集故事"})
    assert single.status_code == 201
    assert [item["name"] for item in single.json()["items"]] == ["单集故事"]

    page = listed(client, "owner")
    assert page["total"] == 3
    assert page["limit"] == 20
    assert {item["id"] for item in page["items"]} == {body["id"], single.json()["id"]}
    assert set(page["items"][0]) == {"id", "name", "badge", "badgeCls", "line"}
    assert all(item["line"].startswith("2026-11-21 · 查询中") for item in page["items"])

    assert listed(client, "other")["items"] == []
    assert listed(client, "other")["total"] == 0
    hidden = client.get(f"/api/v1/queries/{body['id']}", headers=client.auth("other"))
    assert hidden.status_code == 404
    blocked = record(client, "other", body["id"], {"name": "海边故事"})
    assert blocked.status_code == 404
    session.expire_all()
    stored = session.scalars(select(QueryProgram).where(QueryProgram.query_id == body["id"])).all()
    assert {row.resolution for row in stored} == {"pending"}


def test_large_batch_lists_every_uploaded_name_across_pages(session, clock):
    add_user(session, "owner", clock)
    add_user(session, "other", clock)
    session.commit()
    client = make_client(session, clock)
    names = [f"节目 {index}" for index in range(27)]
    query_id = create(client, "owner", {"names": names}).json()["id"]
    first = listed(client, "owner")
    next_page = listed(client, "owner", limit=21, offset=19)
    assert first["items"][-1] == next_page["items"][0]
    combined = first["items"] + next_page["items"][1:]
    assert [row["name"] for row in combined] == names
    assert {row["id"] for row in combined} == {query_id}
    assert listed(client, "other", limit=21, offset=19)["items"] == []


def test_list_is_newest_first_and_paged_by_program(session, clock):
    add_user(session, "owner", clock)
    session.commit()

    class Clock:
        def __init__(self, now):
            self.now = now

        def __call__(self):
            return self.now

    holder = Clock(clock)
    from nadou.db import Database
    from nadou.main import create_app
    from tests.user_helpers import UserClient

    client = UserClient(
        create_app(database=Database(engine=session.get_bind()), now=holder),
        session,
        clock,
    )
    first = create(client, "owner", {"names": ["第一节目"]})
    assert first.status_code == 201
    holder.now = clock + timedelta(hours=1)
    second = create(client, "owner", {"names": ["第二甲", "第二乙"]})
    assert second.status_code == 201

    page = listed(client, "owner", limit=1)
    assert [row["name"] for row in page["items"]] == ["第二甲"]
    assert page["total"] == 3
    second = listed(client, "owner", limit=1, offset=1)
    assert [row["name"] for row in second["items"]] == ["第二乙"]
    third = listed(client, "owner", limit=1, offset=2)
    assert [row["name"] for row in third["items"]] == ["第一节目"]
    rejected = client.get("/api/v1/queries", params={"limit": 0}, headers=client.auth("owner"))
    assert rejected.status_code == 422


def test_empty_and_unknown_names_are_rejected(session, clock):
    add_user(session, "owner", clock)
    session.commit()
    client = make_client(session, clock)
    assert create(client, "owner", {}).status_code == 400
    assert create(client, "owner", {"names": ["  ", ""]}).status_code == 400
    assert create(client, "owner", {"name": "x" * 201}).status_code == 400
    created = create(client, "owner", {"name": "海边故事"})
    missing = record(client, "owner", created.json()["id"], {"name": "别的节目"})
    assert missing.status_code == 400
    forged = record(
        client,
        "owner",
        created.json()["id"],
        {"name": "海边故事", "checked_at": "2001-02-03"},
    )
    assert forged.status_code == 422


def test_summary_uses_submission_date_and_real_hits_only(session, clock):
    add_user(session, "owner", clock, "vip")
    when = at_age(clock, 100)
    add_record(
        session,
        task_id="t-real",
        record_id="rec-real",
        program="海边故事",
        when=when,
        platform="山东移动",
    )
    session.get(Task, "t-real").platform_operator = "移动"
    session.get(Task, "t-real").platform_province = "山东"
    add_generated(
        session,
        record_id="rec-gen",
        program="只有生成",
        when=datetime(2001, 2, 3, 8, 0, tzinfo=RecordAge.TZ),
    )
    add_record(
        session,
        task_id="t-undated",
        record_id="rec-undated",
        program="没有日期",
        when=None,
    )
    add_record(
        session,
        task_id="t-recent",
        record_id="rec-recent",
        program="最新节目",
        when=at_age(clock, 5),
        platform="示例视频",
    )
    add_record(
        session,
        task_id="t-dispute",
        record_id="rec-dispute",
        program="争议节目",
        when=when,
        hit_type="dispute",
    )
    session.add(
        Task(
            task_id="t-flagged",
            status="done",
            created_at=datetime(2018, 5, 5, 9, 0, tzinfo=RecordAge.TZ),
            raw_filename="flagged.mp4",
            platform_name="示例视频",
            platform_operator="电信",
            platform_province="广东",
        )
    )
    session.add(
        IndexRecord(
            id="rec-flagged",
            task_id="t-flagged",
            program_name="带任务的生成",
            program_name_raw="带任务的生成",
            hit_type="consensus",
            frame_count=1,
            timestamps=["00:00:01"],
            uploaded_at=None,
            generated_uploaded_at=datetime(2001, 2, 3, 8, 0, tzinfo=RecordAge.TZ),
            date_generated=True,
        )
    )
    session.commit()
    client = make_client(session, clock)
    created = create(
        client,
        "owner",
        {
            "names": [
                "海边故事",
                "只有生成",
                "没有日期",
                "最新节目",
                "争议节目",
                "带任务的生成",
                "电影",
            ]
        },
    )
    assert created.status_code == 201, created.text
    query_id = created.json()["id"]
    for name in (
        "海边故事",
        "只有生成",
        "没有日期",
        "最新节目",
        "争议节目",
        "带任务的生成",
        "电影",
    ):
        saved = record(client, "owner", query_id, {"name": name})
        assert saved.status_code == 200, saved.text

    detail = client.get(f"/api/v1/queries/{query_id}", headers=client.auth("owner"))
    assert detail.status_code == 200
    text = detail.text
    assert "2001-02-03" not in text
    assert "2018-05-05" not in text
    assert when.astimezone(RecordAge.TZ).strftime("%Y-%m-%d") not in text
    assert at_age(clock, 5).astimezone(RecordAge.TZ).strftime("%Y-%m-%d") not in text
    by_name = {item["name"]: item for item in detail.json()["items"]}

    hit = by_name["海边故事"]
    assert hit["badge"] == "已命中"
    assert hit["badgeCls"] == "ok"
    assert hit["hit"] is True
    assert hit["line"] == "命中 1 条 · 涉及 1 个平台"
    assert hit["evidenceName"] == "海边故事"
    assert hit["evidencePlatform"] == "山东移动"

    generated = by_name["只有生成"]
    assert generated["badge"] == "未命中"
    assert generated["missed"] is True
    assert generated["line"] == ResultSummary.MISS_LINE
    assert generated["evidenceName"] is None
    assert generated["evidencePlatform"] is None

    assert by_name["带任务的生成"]["badge"] == "未命中"
    assert by_name["没有日期"]["badge"] == "未命中"
    assert "有最新记录" not in by_name["没有日期"]["line"]
    assert by_name["最新节目"]["badge"] == "已命中"
    assert by_name["最新节目"]["line"] == ResultSummary.RECENT_LINE
    assert by_name["争议节目"]["badge"] == "未命中"
    assert by_name["电影"]["badge"] == "未命中"

    rows = listed(client, "owner", limit=100)
    lines = {item["name"]: item["line"] for item in rows["items"] if item["id"] == query_id}
    assert lines["海边故事"] == "2026-11-21 · 已命中 1 条 · 涉及 1 个平台"
    assert lines["只有生成"] == "2026-11-21 · 未命中"
    assert lines["最新节目"] == "2026-11-21 · 有最新记录，可委托取证"


def test_real_hit_plus_generated_row_counts_only_the_real_row(session, clock):
    add_user(session, "owner", clock)
    add_record(
        session,
        task_id="t-mix",
        record_id="rec-mix",
        program="混合节目",
        when=at_age(clock, 100),
        platform="示例视频",
    )
    session.get(Task, "t-mix").platform_operator = "移动"
    session.get(Task, "t-mix").platform_province = "山东"
    add_generated(
        session,
        record_id="rec-mix-gen",
        program="混合节目",
        when=datetime(2001, 2, 3, 8, 0, tzinfo=RecordAge.TZ),
    )
    session.commit()
    client = make_client(session, clock)
    query_id = create(client, "owner", {"name": "混合节目"}).json()["id"]
    saved = record(client, "owner", query_id, {"name": "混合节目"})
    assert saved.status_code == 200
    item = saved.json()["items"][0]
    assert item["line"] == "命中 1 条 · 涉及 1 个平台"
    assert "2001-02-03" not in saved.text


def test_failed_write_back_stores_no_detection_date(session, clock):
    add_user(session, "owner", clock)
    session.commit()
    client = make_client(session, clock)
    query_id = create(client, "owner", {"name": "超时节目"}).json()["id"]
    saved = record(client, "owner", query_id, {"name": "超时节目", "failed": True})
    assert saved.status_code == 200
    item = saved.json()["items"][0]
    assert item["badge"] == "失败"
    assert item["failed"] is True
    assert item["line"] == "查询失败"
    assert saved.json()["failBanner"] == "全部查询失败，请重试。"
    assert saved.json()["showFailBanner"] is True
    page = listed(client, "owner")
    assert page["items"][0]["line"] == "2026-11-21 · 查询失败"


def test_seen_at_is_set_once(session, clock):
    add_user(session, "owner", clock)
    session.commit()

    class Clock:
        def __init__(self, now):
            self.now = now

        def __call__(self):
            return self.now

    holder = Clock(clock)
    from nadou.db import Database
    from nadou.main import create_app
    from tests.user_helpers import UserClient

    client = UserClient(
        create_app(database=Database(engine=session.get_bind()), now=holder),
        session,
        clock,
    )
    query_id = create(client, "owner", {"name": "海边故事"}).json()["id"]
    opened = client.get(f"/api/v1/queries/{query_id}", headers=client.auth("owner"))
    assert opened.json()["seen_at"] is None
    marked = client.post(f"/api/v1/queries/{query_id}/seen", headers=client.auth("owner"))
    assert marked.status_code == 200
    first = marked.json()["seen_at"]
    assert first
    holder.now = clock + timedelta(days=1)
    again = client.post(f"/api/v1/queries/{query_id}/seen", headers=client.auth("owner"))
    assert datetime.fromisoformat(again.json()["seen_at"]) == datetime.fromisoformat(first)
    missing = client.post("/api/v1/queries/missing/seen", headers=client.auth("owner"))
    assert missing.status_code == 404


def test_rows_older_than_the_retention_window_are_still_returned(session, clock):
    add_user(session, "owner", clock)
    session.add(MemberQuery(id="old-q", user_id="owner", created_at=clock - timedelta(days=40)))
    session.add(
        QueryProgram(
            query_id="old-q",
            position=0,
            uploaded_name="旧节目",
            resolution="pending",
        )
    )
    session.commit()
    client = make_client(session, clock)
    rows = listed(client, "owner")
    assert rows["total"] == 1
    assert rows["items"][0]["id"] == "old-q"
    assert rows["items"][0]["name"] == "旧节目"


def test_legacy_query_without_user_is_hidden_from_everyone(session, clock):
    add_user(session, "member", clock, "vip")
    add_user(session, "free-user", clock)
    add_query(
        session,
        clock,
        "legacy-hidden",
        [{"uploaded": "旧查询节目", "resolution": "exact"}],
        user_id=None,
    )
    add_query(
        session,
        clock,
        "owned-q",
        [{"uploaded": "我的节目", "resolution": "pending"}],
        user_id="member",
    )
    session.commit()
    client = make_client(session, clock)

    assert export(client, "legacy-hidden", "member").status_code == 404
    assert export(client, "legacy-hidden", "free-user").status_code == 404
    member = client.auth("member")
    assert client.get("/api/v1/queries/legacy-hidden", headers=member).status_code == 404
    listed_rows = client.get("/api/v1/queries", headers=member)
    assert listed_rows.status_code == 200
    assert [item["id"] for item in listed_rows.json()["items"]] == ["owned-q"]
    assert "legacy-hidden" not in listed_rows.text
    free = client.auth("free-user")
    assert client.get("/api/v1/queries/legacy-hidden", headers=free).status_code == 404
    free_rows = client.get("/api/v1/queries", headers=free)
    assert free_rows.json()["items"] == []
    assert free_rows.json()["total"] == 0


def test_export_is_limited_to_the_owner(session, clock):
    add_user(session, "owner", clock, "vip")
    add_user(session, "other-vip", clock, "vip")
    add_user(session, "free-owner", clock)
    add_record(
        session,
        task_id="t-export",
        record_id="rec-export",
        program="导出节目",
        when=at_age(clock, 100),
    )
    add_query(
        session,
        clock,
        "legacy-q",
        [{"uploaded": "导出节目", "resolution": "exact"}],
        user_id=None,
    )
    session.commit()
    client = make_client(session, clock)
    query_id = create(client, "owner", {"name": "导出节目"}).json()["id"]
    assert record(client, "owner", query_id, {"name": "导出节目"}).status_code == 200
    owned = export(client, query_id, "owner")
    assert owned.status_code == 200
    assert owned.content.startswith(b"PK")
    assert export(client, query_id, "other-vip").status_code == 404
    free_id = create(client, "free-owner", {"name": "导出节目"}).json()["id"]
    assert export(client, free_id, "free-owner").status_code == 403
    assert export(client, "legacy-q", "owner").status_code == 404


def test_search_does_not_write_a_query(session, clock):
    add_user(session, "owner", clock)
    session.commit()
    client = make_client(session, clock)
    before = session.scalar(select(func.count()).select_from(MemberQuery))
    found = client.get(
        "/api/v1/programs/search",
        params={"name": "海边故事"},
        headers=client.auth("owner"),
    )
    assert found.status_code == 200
    session.expire_all()
    assert session.scalar(select(func.count()).select_from(MemberQuery)) == before


def test_cleanup_index_covers_user_and_created_at():
    indexes = {
        index.name: [column.name for column in index.columns]
        for index in MemberQuery.__table__.indexes
    }
    assert indexes["ix_member_queries_user_id_created_at"] == ["user_id", "created_at"]


def test_summary_source_does_not_read_generated_timestamps():
    source = (
        Path(__file__)
        .resolve()
        .parents[1]
        .joinpath("src/nadou/query_records.py")
        .read_text(encoding="utf-8")
    )
    assert "generated_uploaded_at" not in source
    assert "tier_time" not in source
    assert ".date_generated" not in source
    assert "reportable" in source
