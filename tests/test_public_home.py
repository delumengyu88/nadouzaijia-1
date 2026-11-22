from datetime import datetime, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from nadou.db import Base, Database
from nadou.main import create_app
from nadou.models import Announcement, HomepageExample, IndexRecord, Task
from nadou.platform_names import PlatformNames
from nadou.text import RecordAge

BEIJING = ZoneInfo("Asia/Shanghai")


def snapshot(session) -> dict[str, int]:
    session.rollback()
    return {
        table.name: session.scalar(select(func.count()).select_from(table))
        for table in Base.metadata.sorted_tables
    }


def at_age(clock, days, hour=11):
    day = clock.astimezone(RecordAge.TZ).date() - timedelta(days=days)
    return datetime(day.year, day.month, day.day, hour, 0, 0, tzinfo=RecordAge.TZ)


def day_of(when) -> str:
    return when.astimezone(RecordAge.TZ).date().isoformat()


def store(
    session,
    *,
    task_id,
    record_id,
    program,
    platform="广东移动",
    when=None,
    generated_at=None,
    with_task=True,
    created_at=True,
    status="done",
    is_test=False,
    hit_type="consensus",
    network="公网",
    unverified=False,
    platform_name=None,
    platform_province=None,
    platform_operator=None,
    source_unverified=None,
):
    parsed = PlatformNames.normalize(None if unverified else platform)
    name = parsed.name if platform_name is None else platform_name
    province = parsed.province if platform_province is None else platform_province
    operator = parsed.operator if platform_operator is None else platform_operator
    flagged = parsed.unrecognized if source_unverified is None else source_unverified
    if with_task:
        session.add(
            Task(
                task_id=task_id,
                status=status,
                created_at=when if created_at else None,
                raw_filename=f"{task_id}.mp4",
                network_type=None if unverified else network,
                platform_name=name,
                platform_province=province,
                platform_operator=operator,
                platform_subtype=parsed.subtype,
                source_unverified=flagged or unverified,
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
            frame_count=2,
            timestamps=["00:01:02"],
            uploaded_at=None,
            generated_uploaded_at=generated_at,
            date_generated=generated_at is not None,
        )
    )


def add_example(session, *, example_id, name, sort_order, online=True, detected_on=None):
    session.add(
        HomepageExample(
            id=example_id,
            program_name=name,
            record_id=None,
            sort_order=sort_order,
            online=online,
            platform_hits=77,
            detected_on=detected_on,
        )
    )


def add_note(session, *, note_id, title, starts, ends, online=True, jump=None, frequency="once"):
    session.add(
        Announcement(
            id=note_id,
            title=title,
            body=f"{title}的正文",
            button_text="我知道了",
            jump_label=jump,
            starts_at=starts,
            ends_at=ends,
            frequency=frequency,
            online=online,
            created_at=starts,
        )
    )


def example_url(name: str) -> str:
    return "/api/v1/app/examples/" + quote(name, safe="")


def test_home_empty_needs_no_login_and_writes_nothing(session, clock):
    app = create_app(database=Database(engine=session.get_bind()), now=lambda: clock)
    before = snapshot(session)
    with TestClient(app) as client:
        bare = client.get("/api/v1/app/home")
        bogus = client.get("/api/v1/app/home", headers={"Authorization": "Bearer not-a-session"})
        missing = client.get(example_url("没有这个示例"))
        blank = client.get(example_url("   "))
    assert bare.request.headers.get("authorization") is None
    assert bare.status_code == 200
    assert bare.headers["cache-control"] == "public, max-age=60"
    assert bare.json() == {"announcements": [], "examples": [], "platformTotal": 0}
    assert bogus.status_code == 200
    assert bogus.json() == bare.json()
    for response in (missing, blank):
        assert response.status_code == 404
        assert response.json() == {"detail": "example not found"}
        assert response.headers["cache-control"] == "public, max-age=60"
    assert snapshot(session) == before


def test_home_hides_offline_and_out_of_window_content(workbench):
    now = workbench.clock.now
    add_note(
        workbench.session,
        note_id="ann-live",
        title="生效公告",
        starts=now - timedelta(days=1),
        ends=now + timedelta(days=1),
        jump="去首页",
        frequency="every",
    )
    add_note(
        workbench.session,
        note_id="ann-older",
        title="更早公告",
        starts=now - timedelta(days=5),
        ends=now + timedelta(days=2),
    )
    add_note(
        workbench.session,
        note_id="ann-ended",
        title="刚过期",
        starts=now - timedelta(days=10),
        ends=now,
    )
    add_note(
        workbench.session,
        note_id="ann-future",
        title="未开始",
        starts=now + timedelta(minutes=1),
        ends=now + timedelta(days=3),
    )
    add_note(
        workbench.session,
        note_id="ann-off",
        title="已下线",
        starts=now - timedelta(days=1),
        ends=now + timedelta(days=1),
        online=False,
    )
    add_example(workbench.session, example_id="ex-off", name="下线示例", sort_order=0, online=False)
    add_example(workbench.session, example_id="ex-b", name="后一个", sort_order=2)
    add_example(workbench.session, example_id="ex-a", name="前一个", sort_order=1)
    workbench.session.commit()
    before = snapshot(workbench.session)

    response = workbench.client.get("/api/v1/app/home")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=60"
    body = response.json()
    assert [item["title"] for item in body["announcements"]] == ["生效公告", "更早公告"]
    live = body["announcements"][0]
    assert live["body"] == "生效公告的正文"
    assert live["button_text"] == "我知道了"
    assert live["jump_label"] == "去首页"
    assert live["frequency"] == "every"
    assert body["announcements"][1]["jump_label"] is None
    assert [item["name"] for item in body["examples"]] == ["前一个", "后一个"]
    assert body["examples"][0] == {
        "name": "前一个",
        "line": "涉及 0 个平台",
        "sub": "命中 0 条记录",
    }
    assert "下线示例" not in response.text
    assert "刚过期" not in response.text
    assert "未开始" not in response.text
    assert "已下线" not in response.text
    assert "77" not in response.text
    closed = workbench.client.get(example_url("下线示例"))
    assert closed.status_code == 404
    assert snapshot(workbench.session) == before


def test_platform_total_counts_province_and_operator(workbench):
    clock = workbench.clock.now
    store(
        workbench.session,
        task_id="gd-ott",
        record_id="gd-ott-rec",
        program="甲",
        platform="广东移动OTT",
        when=at_age(clock, 10),
    )
    store(
        workbench.session,
        task_id="gd-best",
        record_id="gd-best-rec",
        program="乙",
        platform="广东移动百视通",
        when=at_age(clock, 200),
        network="专网",
    )
    store(
        workbench.session,
        task_id="zj-public",
        record_id="zj-public-rec",
        program="甲",
        platform="浙江移动",
        when=at_age(clock, 200),
    )
    store(
        workbench.session,
        task_id="zj-private",
        record_id="zj-private-rec",
        program="乙",
        platform="浙江移动",
        when=at_age(clock, 40),
        network="专网",
    )
    store(
        workbench.session,
        task_id="standalone",
        record_id="standalone-rec",
        program="甲",
        platform="示例视频",
        when=at_age(clock, 80),
    )
    store(
        workbench.session,
        task_id="young-only",
        record_id="young-only-rec",
        program="甲",
        platform="福建移动",
        when=at_age(clock, 10),
    )
    store(
        workbench.session,
        task_id="generated-platform",
        record_id="generated-platform-rec",
        program="甲",
        platform="山东移动",
        when=at_age(clock, 250),
        generated_at=datetime(2033, 3, 3, 3, 3, tzinfo=BEIJING),
    )
    store(
        workbench.session,
        task_id="unknown",
        record_id="unknown-rec",
        program="甲",
        platform="没有这个平台",
        when=at_age(clock, 80),
        unverified=True,
    )
    store(
        workbench.session,
        task_id="other-label",
        record_id="other-label-rec",
        program="甲",
        when=at_age(clock, 80),
        platform_name=PlatformNames.OTHER,
        platform_province="广东",
        platform_operator="移动",
        source_unverified=False,
    )
    store(
        workbench.session,
        task_id="unrecognized-label",
        record_id="unrecognized-label-rec",
        program="甲",
        when=at_age(clock, 80),
        platform_name=PlatformNames.UNRECOGNIZED,
        platform_province="北京",
        platform_operator="联通",
        source_unverified=False,
    )
    store(
        workbench.session,
        task_id="flagged",
        record_id="flagged-rec",
        program="甲",
        platform="江苏移动",
        when=at_age(clock, 80),
        source_unverified=True,
    )
    store(
        workbench.session,
        task_id="test-row",
        record_id="test-row-rec",
        program="甲",
        platform="上海移动",
        when=at_age(clock, 80),
        is_test=True,
    )
    store(
        workbench.session,
        task_id="cancelled",
        record_id="cancelled-rec",
        program="甲",
        platform="天津移动",
        when=at_age(clock, 80),
        status="cancelled",
    )
    store(
        workbench.session,
        task_id="duplicate",
        record_id="duplicate-rec",
        program="甲",
        platform="河北移动",
        when=at_age(clock, 80),
        status="duplicate",
    )
    store(
        workbench.session,
        task_id="dispute",
        record_id="dispute-rec",
        program="甲",
        platform="四川移动",
        when=at_age(clock, 80),
        hit_type="dispute",
    )
    store(
        workbench.session,
        task_id="review",
        record_id="review-rec",
        program="甲",
        platform="河南移动",
        when=at_age(clock, 80),
        hit_type="review",
    )
    store(
        workbench.session,
        task_id="orphan-real",
        record_id="orphan-real-rec",
        program="甲",
        when=at_age(clock, 80),
        with_task=False,
    )
    workbench.session.commit()
    before = snapshot(workbench.session)

    body = workbench.client.get("/api/v1/app/home").json()
    assert body["platformTotal"] == 5
    assert snapshot(workbench.session) == before


def test_examples_use_real_dates_and_skip_generated_rows(workbench):
    clock = workbench.clock.now
    program = "海边来信"
    recent = at_age(clock, 40)
    older = at_age(clock, 100)
    middle = at_age(clock, 80)
    young = at_age(clock, 10)
    generated_day = datetime(2033, 3, 3, 3, 3, tzinfo=BEIJING)
    stored_day = datetime(1999, 1, 2, tzinfo=BEIJING)
    shandong = at_age(clock, 250)
    add_example(
        workbench.session,
        example_id="ex-empty",
        name="空示例",
        sort_order=1,
        detected_on=stored_day,
    )
    add_example(
        workbench.session,
        example_id="ex-show",
        name=program,
        sort_order=2,
        detected_on=stored_day,
    )
    store(
        workbench.session,
        task_id="hit-gd",
        record_id="hit-gd",
        program=program,
        platform="广东移动",
        when=older,
    )
    store(
        workbench.session,
        task_id="hit-zj",
        record_id="hit-zj",
        program=program,
        platform="浙江移动",
        when=recent,
        network="专网",
    )
    store(
        workbench.session,
        task_id="hit-ott",
        record_id="hit-ott",
        program=program,
        platform="广东移动OTT",
        when=middle,
    )
    store(
        workbench.session,
        task_id="hit-bad",
        record_id="hit-bad",
        program=program,
        platform="没有这个平台",
        when=at_age(clock, 50),
        unverified=True,
    )
    store(
        workbench.session,
        task_id="hit-young",
        record_id="hit-young",
        program=program,
        platform="江苏移动",
        when=young,
    )
    store(
        workbench.session,
        task_id="hit-generated",
        record_id="hit-generated",
        program=program,
        platform="山东移动",
        when=shandong,
        generated_at=generated_day,
    )
    store(
        workbench.session,
        task_id="hit-orphan-generated",
        record_id="hit-orphan-generated",
        program=program,
        generated_at=generated_day,
        with_task=False,
    )
    workbench.session.commit()
    before = snapshot(workbench.session)

    home = workbench.client.get("/api/v1/app/home")
    assert home.status_code == 200
    body = home.json()
    assert body["platformTotal"] == 4
    assert body["examples"][0]["name"] == "空示例"
    assert body["examples"][0]["sub"] == "命中 0 条记录"
    assert body["examples"][1] == {
        "name": program,
        "line": "涉及 2 个平台",
        "sub": f"命中 4 条记录 · {day_of(recent)}",
    }
    assert day_of(young) not in body["examples"][1]["sub"]
    assert day_of(clock) not in home.text
    assert "1999-01-02" not in home.text
    assert "2033-03-03" not in home.text
    assert day_of(shandong) not in home.text
    assert "77" not in home.text

    report = workbench.client.get(
        example_url(program),
        headers={"Authorization": "Bearer not-a-session"},
    )
    assert report.status_code == 200
    assert report.headers["cache-control"] == "public, max-age=60"
    detail = report.json()
    assert detail["name"] == program
    assert detail["program_name"] == program
    assert detail["example"] is True
    assert detail["outcome"] == "hit"
    assert detail["platform_count"] == 2
    assert detail["unverified_source_count"] == 1
    assert detail["line"] == body["examples"][1]["line"]
    assert detail["sub"] == body["examples"][1]["sub"]
    assert detail["evidence_platforms"] == ["浙江移动", "广东移动"]
    assert detail["evidence_program_name"] == program
    assert [row["record_id"] for row in detail["records"]] == [
        "hit-young",
        "hit-zj",
        "hit-bad",
        "hit-ott",
        "hit-gd",
    ]
    assert "hit-generated" not in report.text
    assert "2033-03-03" not in report.text
    assert day_of(shandong) not in report.text
    young_row = detail["records"][0]
    assert young_row["platform"] == "江苏移动"
    assert young_row["age_bucket"] == "lt_30"
    assert datetime.fromisoformat(young_row["checked_at"]) == young
    latest = detail["records"][1]
    assert latest["platform"] == "浙江移动"
    assert latest["network_type"] == "专网"
    assert latest["age_bucket"] == "d30_89"
    assert datetime.fromisoformat(latest["checked_at"]) == recent
    assert detail["records"][2]["platform"] == "其他平台"
    assert detail["records"][2]["platform_operator"] is None
    for row in detail["records"]:
        assert row["thumbnail_url"] is None
        assert row["hd_url"] is None
        assert row["access"] is None
        assert row["can_download_report"] is None
        assert row["can_download_screenshot"] is None
        assert row["locked"] is None
    folded = workbench.client.get(example_url("  海边来信  "))
    assert folded.status_code == 200
    assert folded.json()["name"] == program
    assert snapshot(workbench.session) == before


def test_example_name_is_folded_and_young_only_has_no_fallback_date(workbench):
    clock = workbench.clock.now
    when = at_age(clock, 10)
    add_example(workbench.session, example_id="ex-fold", name="AbC节目", sort_order=1)
    store(
        workbench.session,
        task_id="fold-task",
        record_id="fold-rec",
        program="abc节目",
        platform="广东移动",
        when=when,
    )
    workbench.session.commit()

    home = workbench.client.get("/api/v1/app/home").json()
    assert home["examples"] == [
        {"name": "AbC节目", "line": "涉及 0 个平台", "sub": "命中 0 条记录"}
    ]
    assert day_of(clock) not in home["examples"][0]["sub"]
    assert day_of(when) not in home["examples"][0]["sub"]
    report = workbench.client.get(example_url("abc节目"))
    assert report.status_code == 200
    body = report.json()
    assert body["name"] == "AbC节目"
    assert body["program_name"] == "abc节目"
    assert body["outcome"] == "recent_only"
    assert body["evidence_platforms"] == []
    assert body["evidence_program_name"] == "AbC节目"
    assert body["records"][0]["record_id"] == "fold-rec"
    assert body["records"][0]["checked_at"] is not None
    assert datetime.fromisoformat(body["records"][0]["checked_at"]) == when
    assert home["platformTotal"] == 1
