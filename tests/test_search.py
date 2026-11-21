import json
from datetime import datetime, timedelta

from sqlalchemy import select

from nadou.models import Entitlement, IndexRecord, PlatformAlias, ProgramAlias, Task, User
from nadou.records import IndexRecordStore
from nadou.text import RecordAge

PROGRAM = "边界节目"
LOCKED_KEYS = {"record_id", "age_bucket", "locked", "lock_reason"}


def at_age(clock, days: int):
    day = clock.astimezone(RecordAge.TZ).date() - timedelta(days=days)
    return datetime_at(day, 11, days % 60)


def datetime_at(day, hour, second):
    return datetime(day.year, day.month, day.day, hour, 0, second, tzinfo=RecordAge.TZ)


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


def add_hit(
    session,
    *,
    task_id,
    record_id,
    program,
    when,
    status="done",
    platform="示例视频",
    network="公网",
    unverified=False,
    is_test=False,
    filename=None,
    with_task=True,
    created_at=True,
    uploaded_at=None,
    hit_type="consensus",
):
    if with_task:
        session.add(
            Task(
                task_id=task_id,
                status=status,
                created_at=when if created_at else None,
                file_uploaded_at=uploaded_at,
                raw_filename=filename or f"{task_id}.mp4",
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
            frame_count=2,
            timestamps=["00:01:02"],
            uploaded_at=None if with_task else when,
        )
    )


def by_id(body):
    return {row["record_id"]: row for row in body["records"]}


def test_server_filters_every_tier(session, client, clock):
    ages = (29, 30, 89, 90, 179, 180)
    for age in ages:
        add_hit(
            session,
            task_id=f"task-age-{age}",
            record_id=f"rec-{age}",
            program=PROGRAM,
            when=at_age(clock, age),
            platform=f"plat{age}",
        )
    add_user(session, "user-regular", clock, "regular")
    add_user(session, "user-vip", clock, "vip")
    add_user(session, "user-p90", clock, "program_90", program=PROGRAM)
    add_user(session, "user-p30", clock, "program_30", program=PROGRAM)
    for age in ages:
        add_user(session, "user-shot", clock, "screenshot", record_id=f"rec-{age}")
    add_user(session, "user-expired", clock, "vip", expired=True)
    add_hit(
        session,
        task_id="task-other",
        record_id="rec-other",
        program="其他节目",
        when=at_age(clock, 100),
        platform="plat-other",
    )

    def fetch(user_id=None):
        session.commit()
        headers = {"X-User-Id": user_id} if user_id else {}
        response = client.get("/api/v1/programs/search", params={"name": PROGRAM}, headers=headers)
        assert response.status_code == 200, response.text
        return response.json()

    free = fetch()
    _assert_shape(free, "free")
    vip = fetch("user-vip")
    _assert_shape(vip, "vip")
    regular = fetch("user-regular")
    _assert_shape(regular, "regular")
    program_90 = fetch("user-p90")
    _assert_shape(program_90, "program_90")
    program_30 = fetch("user-p30")
    _assert_shape(program_30, "program_30")
    shot = fetch("user-shot")
    _assert_shape(shot, "screenshot")
    expired = fetch("user-expired")
    _assert_shape(expired, "free")

    session.commit()
    other = client.get(
        "/api/v1/programs/search",
        params={"name": "其他节目"},
        headers={"X-User-Id": "user-p90"},
    ).json()
    assert other["records"][0]["locked"] is True
    assert "plat-other" not in json.dumps(other)

    for body in (free, vip, regular, program_90, program_30, shot, expired):
        raw = json.dumps(body)
        assert "http://" not in raw
        assert "https://" not in raw
        assert "zip" not in raw.casefold()
        assert ".jpg" not in raw
        for age in ages:
            assert f"task-age-{age}" not in raw


def _assert_shape(body, tier):
    rows = by_id(body)
    assert body["outcome"] == "hit"
    assert body["platform_count"] == 5
    assert body["unverified_source_count"] == 0
    hidden_dates = set()
    for age, row in ((age, rows[f"rec-{age}"]) for age in (29, 30, 89, 90, 179, 180)):
        if row["locked"]:
            assert set(row) == LOCKED_KEYS
            assert f"plat{age}" not in json.dumps(row)
            hidden_dates.add(age)
            if age < 30:
                assert row["lock_reason"] == "有最新记录，可委托取证"
                assert row["age_bucket"] == "lt_30"
            elif age < 90:
                assert row["lock_reason"] == "此记录需 VIP 或单节目 30 天档查看"
                assert row["age_bucket"] == "d30_89"
            else:
                assert row["lock_reason"] == "此记录需普通会员或单节目解锁查看"
        else:
            assert row["locked"] is False
            urls = [row[key] for key in ("thumbnail_url", "hd_url") if key in row]
            assert urls
            assert all(url.startswith("signed://derived/") for url in urls)
    if tier == "free":
        assert rows["rec-180"]["access"] == "lowres"
        assert rows["rec-180"]["platform"] == "plat180"
        assert "hd_url" not in rows["rec-180"]
        assert "/lowres/" in rows["rec-180"]["thumbnail_url"]
        assert rows["rec-180"]["can_download_report"] is False
        assert rows["rec-90"]["locked"] is True
        assert rows["rec-30"]["locked"] is True
        assert hidden_dates == {29, 30, 89, 90, 179}
    elif tier == "vip":
        assert rows["rec-29"]["locked"] is True
        assert rows["rec-30"]["access"] == "full"
        assert rows["rec-30"]["platform"] == "plat30"
        assert rows["rec-30"]["can_download_report"] is True
        assert "/hd/" in rows["rec-30"]["hd_url"]
        assert hidden_dates == {29}
    elif tier == "regular":
        assert rows["rec-89"]["locked"] is True
        assert rows["rec-90"]["access"] == "full"
        assert rows["rec-90"]["can_download_report"] is True
        assert rows["rec-180"]["can_download_screenshot"] is True
        assert hidden_dates == {29, 30, 89}
    elif tier == "program_90":
        assert rows["rec-89"]["locked"] is True
        assert rows["rec-90"]["access"] == "full"
        assert rows["rec-90"]["can_download_report"] is False
        assert rows["rec-90"]["can_download_screenshot"] is False
        assert rows["rec-90"]["platform"] == "plat90"
        assert hidden_dates == {29, 30, 89}
    elif tier == "program_30":
        assert rows["rec-29"]["locked"] is True
        assert rows["rec-30"]["can_download_report"] is False
        assert rows["rec-30"]["can_download_screenshot"] is False
        assert rows["rec-30"]["platform"] == "plat30"
        assert hidden_dates == {29}
    elif tier == "screenshot":
        assert rows["rec-89"]["locked"] is True
        assert rows["rec-90"]["access"] == "screenshot"
        assert "platform" not in rows["rec-90"]
        assert "checked_at" not in rows["rec-90"]
        assert rows["rec-90"]["can_download_report"] is False
        assert rows["rec-90"]["can_download_screenshot"] is True
        assert "/hd/" in rows["rec-90"]["hd_url"]
        assert rows["rec-180"]["access"] == "lowres"
        assert rows["rec-180"]["platform"] == "plat180"
        assert rows["rec-180"]["can_download_report"] is False
        assert rows["rec-180"]["can_download_screenshot"] is True
        assert "hd_url" in rows["rec-180"]
        assert hidden_dates == {29, 30, 89}


def test_counts_exclusions_and_fallbacks(session, client, clock):
    session.add(ProgramAlias(alias="边界别名", canonical_name=PROGRAM))
    session.add(PlatformAlias(alias="平台甲ott", canonical_name="平台甲"))
    add_hit(
        session,
        task_id="keep-a",
        record_id="rec-a",
        program=PROGRAM,
        when=at_age(clock, 100),
        platform="平台甲",
    )
    add_hit(
        session,
        task_id="keep-b",
        record_id="rec-b",
        program=PROGRAM,
        when=at_age(clock, 100),
        platform="平台甲",
        network="专网",
    )
    add_hit(
        session,
        task_id="fresh",
        record_id="rec-fresh",
        program=PROGRAM,
        when=at_age(clock, 10),
        platform="平台乙",
    )
    add_hit(
        session,
        task_id="unknown-src",
        record_id="rec-unk",
        program=PROGRAM,
        when=at_age(clock, 60),
        unverified=True,
    )
    add_hit(
        session,
        task_id="cancelled",
        record_id="rec-cancel",
        program=PROGRAM,
        when=at_age(clock, 200),
        status="cancelled",
        platform="平台丙",
    )
    add_hit(
        session,
        task_id="dup",
        record_id="rec-dup",
        program=PROGRAM,
        when=at_age(clock, 200),
        status="duplicate",
        platform="平台丁",
    )
    add_hit(
        session,
        task_id="test-file",
        record_id="rec-test",
        program=PROGRAM,
        when=at_age(clock, 200),
        is_test=True,
        filename="test_demo.mp4",
        platform="平台戊",
    )
    add_hit(
        session,
        task_id="orphan",
        record_id="rec-orphan",
        program=PROGRAM,
        when=at_age(clock, 200),
        with_task=False,
    )
    add_hit(
        session,
        task_id="fallback",
        record_id="rec-fallback",
        program=PROGRAM,
        when=at_age(clock, 10),
        created_at=False,
        uploaded_at=at_age(clock, 200),
        platform="平台己",
    )
    session.commit()
    response = client.get("/api/v1/programs/search", params={"name": "  边界别名  "})
    assert response.status_code == 200
    body = response.json()
    assert body["program_name"] == PROGRAM
    assert body["outcome"] == "hit"
    assert body["platform_count"] == 2
    assert body["unverified_source_count"] == 2
    ids = set(by_id(body))
    assert ids == {"rec-a", "rec-b", "rec-fresh", "rec-unk", "rec-orphan", "rec-fallback"}
    assert by_id(body)["rec-fresh"]["locked"] is True
    assert by_id(body)["rec-fresh"]["age_bucket"] == "lt_30"
    assert "平台乙" not in json.dumps(body)
    assert by_id(body)["rec-orphan"]["access"] == "lowres"
    assert by_id(body)["rec-orphan"]["platform"] == "来源待核实"
    assert by_id(body)["rec-fallback"]["access"] == "lowres"
    assert by_id(body)["rec-fallback"]["platform"] == "平台己"
    assert "checked_at" in by_id(body)["rec-fallback"]


def test_recent_only_and_miss(session, client, clock):
    add_hit(
        session,
        task_id="only-new",
        record_id="rec-new",
        program="只有新片",
        when=at_age(clock, 5),
        platform="保密平台",
    )
    session.commit()
    recent = client.get("/api/v1/programs/search", params={"name": "只有新片"}).json()
    assert recent["outcome"] == "recent_only"
    assert recent["platform_count"] == 0
    assert recent["unverified_source_count"] == 0
    assert recent["records"][0]["lock_reason"] == "有最新记录，可委托取证"
    assert "保密平台" not in json.dumps(recent)

    add_hit(
        session,
        task_id="only-cancel",
        record_id="rec-gone",
        program="取消片",
        when=at_age(clock, 100),
        status="cancelled",
    )
    session.commit()
    missed = client.get("/api/v1/programs/search", params={"name": "取消片"}).json()
    assert missed["outcome"] == "miss"
    assert missed["records"] == []
    assert missed["platform_count"] == 0


def test_created_at_beats_upload_time(session, client, clock):
    add_hit(
        session,
        task_id="prefer-created",
        record_id="rec-prefer",
        program="时间片",
        when=at_age(clock, 10),
        uploaded_at=at_age(clock, 200),
        platform="不应出现",
    )
    session.commit()
    body = client.get("/api/v1/programs/search", params={"name": "时间片"}).json()
    assert body["outcome"] == "recent_only"
    assert body["records"][0]["age_bucket"] == "lt_30"
    assert "不应出现" not in json.dumps(body)


def test_screenshot_does_not_spill_and_unknown_user(session, client, clock):
    add_hit(
        session,
        task_id="shot-task",
        record_id="rec-shot",
        program=PROGRAM,
        when=at_age(clock, 100),
        platform="只给这一张",
    )
    add_hit(
        session,
        task_id="other-task",
        record_id="rec-nope",
        program=PROGRAM,
        when=at_age(clock, 100),
        platform="另一张",
    )
    add_user(session, "user-one-shot", clock, "screenshot", record_id="rec-shot")
    session.commit()
    body = client.get(
        "/api/v1/programs/search",
        params={"name": PROGRAM},
        headers={"X-User-Id": "user-one-shot"},
    ).json()
    rows = by_id(body)
    assert rows["rec-shot"]["access"] == "screenshot"
    assert "只给这一张" not in json.dumps(rows["rec-shot"])
    assert rows["rec-nope"]["locked"] is True
    assert "另一张" not in json.dumps(body)
    session.commit()
    missing = client.get(
        "/api/v1/programs/search",
        params={"name": PROGRAM},
        headers={"X-User-Id": "missing-user"},
    )
    assert missing.status_code == 404
    blank = client.get("/api/v1/programs/search", params={"name": "   "})
    assert blank.status_code == 400


def test_unknown_age_is_not_a_fresh_hit(session, client, clock):
    add_hit(
        session,
        task_id="no-time",
        record_id="rec-no-time",
        program="无时间节目",
        when=at_age(clock, 10),
        created_at=False,
        platform="不能当最新",
        unverified=True,
    )
    add_hit(
        session,
        task_id="dated",
        record_id="rec-dated",
        program="无时间节目",
        when=at_age(clock, 100),
        platform="平台甲",
    )
    add_user(session, "user-vip-time", clock, "vip")
    session.commit()
    free = client.get("/api/v1/programs/search", params={"name": "无时间节目"}).json()
    assert free["outcome"] == "hit"
    assert free["platform_count"] == 1
    assert free["unverified_source_count"] == 0
    unknown = by_id(free)["rec-no-time"]
    assert set(unknown) == LOCKED_KEYS
    assert unknown["age_bucket"] == "unknown"
    assert unknown["lock_reason"] == "检测时间待核实"
    assert unknown["lock_reason"] != "有最新记录，可委托取证"
    assert "不能当最新" not in json.dumps(free)
    vip = client.get(
        "/api/v1/programs/search",
        params={"name": "无时间节目"},
        headers={"X-User-Id": "user-vip-time"},
    ).json()
    vip_unknown = by_id(vip)["rec-no-time"]
    assert vip_unknown["locked"] is True
    assert vip_unknown["lock_reason"] == "检测时间待核实"
    assert "不能当最新" not in json.dumps(vip_unknown)
    assert by_id(vip)["rec-dated"]["access"] == "full"

    add_hit(
        session,
        task_id="only-unknown",
        record_id="rec-only-unknown",
        program="只有未知时间",
        when=at_age(clock, 5),
        created_at=False,
        platform="仍不泄露",
    )
    session.commit()
    alone = client.get("/api/v1/programs/search", params={"name": "只有未知时间"}).json()
    assert alone["outcome"] == "miss"
    assert alone["platform_count"] == 0
    assert alone["unverified_source_count"] == 0
    assert alone["records"][0]["age_bucket"] == "unknown"
    assert alone["records"][0]["lock_reason"] == "检测时间待核实"
    assert "仍不泄露" not in json.dumps(alone)
    assert "有最新记录，可委托取证" not in json.dumps(alone)


def test_upsert_canonicalizes_program_alias(session, client, clock):
    session.add(ProgramAlias(alias="ＡＢＣ剧", canonical_name="标准剧"))
    when = at_age(clock, 200)
    session.add(
        Task(
            task_id="alias-task",
            status="done",
            created_at=when,
            raw_filename="alias-task.mp4",
            network_type="公网",
            platform_name="示例视频",
            source_unverified=False,
            is_test=False,
        )
    )
    store = IndexRecordStore(session)
    raw = "  ＡＢＣ剧 "
    stored = store.upsert(
        task_id="alias-task",
        program_name=raw,
        program_name_raw=raw,
        hit_type="consensus",
        frame_count=1,
        timestamps=["00:01:02"],
    )
    again = store.upsert(
        task_id="alias-task",
        program_name="ａｂｃ剧",
        program_name_raw="ａｂｃ剧",
        hit_type="dispute",
        frame_count=4,
        timestamps=["00:02:02"],
    )
    assert stored.id == again.id
    assert stored.program_name == "标准剧"
    assert again.program_name_raw == "ａｂｃ剧"
    assert len(session.scalars(select(IndexRecord)).all()) == 1
    session.commit()
    by_standard = client.get("/api/v1/programs/search", params={"name": "标准剧"}).json()
    by_alias = client.get("/api/v1/programs/search", params={"name": "ａｂｃ剧"}).json()
    assert by_standard["program_name"] == "标准剧"
    assert by_standard["outcome"] == "hit"
    assert by_standard["records"][0]["record_id"] == by_alias["records"][0]["record_id"]
    assert by_standard["records"][0]["platform"] == "示例视频"
    assert by_standard["records"][0]["hit_type"] == "dispute"


def test_no_zip_or_download_route(client):
    paths = [getattr(route, "path", "") for route in client.app.routes]
    assert "/api/v1/programs/search" in paths
    assert not any("zip" in path.lower() or "download" in path.lower() for path in paths)
