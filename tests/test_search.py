import json
from datetime import datetime, timedelta

from sqlalchemy import select

from nadou.models import Entitlement, IndexRecord, Task, User
from nadou.platform_names import PlatformNames
from nadou.program_titles import ProgramTitleGuard
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
                platform_operator=None if unverified else platform,
                platform_province=None,
                platform_subtype=None,
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


def all_rows(body):
    rows = [row for group in body.get("platform_groups", []) for row in group["records"]]
    rows.extend(body.get("locked_records", []))
    return rows


def by_id(body):
    return {row["record_id"]: row for row in all_rows(body)}


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
        headers = client.auth(user_id) if user_id else {}
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
        headers=client.auth("user-p90"),
    ).json()
    assert other["locked_records"][0]["locked"] is True
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
    response = client.get("/api/v1/programs/search", params={"name": "  " + PROGRAM + "  "})
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
    assert by_id(body)["rec-orphan"]["platform"] == "其他平台"
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
    assert recent["locked_records"][0]["lock_reason"] == "有最新记录，可委托取证"
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
    assert missed["platform_groups"] == []
    assert missed["locked_records"] == []
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
    assert body["locked_records"][0]["age_bucket"] == "lt_30"
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
        headers=client.auth("user-one-shot"),
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
        headers=client.auth("missing-user"),
    )
    assert missing.status_code == 401
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
        headers=client.auth("user-vip-time"),
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
    assert alone["locked_records"][0]["age_bucket"] == "unknown"
    assert alone["locked_records"][0]["lock_reason"] == "检测时间待核实"
    assert "仍不泄露" not in json.dumps(alone)
    assert "有最新记录，可委托取证" not in json.dumps(alone)


def test_upsert_canonicalizes_width_and_case(session, client, clock):
    when = at_age(clock, 200)
    session.add(
        Task(
            task_id="fold-task",
            status="done",
            created_at=when,
            raw_filename="fold-task.mp4",
            network_type="公网",
            platform_name="示例视频",
            source_unverified=False,
            is_test=False,
        )
    )
    store = IndexRecordStore(session)
    raw = "  ＡＢＣ剧 "
    stored = store.upsert(
        task_id="fold-task",
        program_name=raw,
        program_name_raw=raw,
        hit_type="consensus",
        frame_count=1,
        timestamps=["00:01:02"],
    )
    again = store.upsert(
        task_id="fold-task",
        program_name="ａｂｃ剧",
        program_name_raw="ａｂｃ剧",
        hit_type="consensus",
        frame_count=4,
        timestamps=["00:02:02"],
    )
    assert stored.id == again.id
    assert stored.program_name == "abc剧"
    assert again.program_name_raw == "ａｂｃ剧"
    assert len(session.scalars(select(IndexRecord)).all()) == 1
    session.commit()
    by_standard = client.get("/api/v1/programs/search", params={"name": "abc剧"}).json()
    by_width = client.get("/api/v1/programs/search", params={"name": "ａｂｃ剧"}).json()
    assert by_standard["program_name"] == "abc剧"
    assert by_standard["outcome"] == "hit"
    visible = by_standard["platform_groups"][0]["records"][0]
    width_visible = by_width["platform_groups"][0]["records"][0]
    assert visible["record_id"] == width_visible["record_id"]
    assert visible["platform"] == "示例视频"
    assert visible["frame_count"] == 4
    assert visible["hit_label"] == "已命中"
    assert "hit_type" not in visible
    assert "match_type" not in json.dumps(by_standard)


def test_locked_summary_by_entitlement(session, client, clock):
    program = "汇总节目"
    ages = (10, 40, 50, 100, 120, 200)
    for age in ages:
        add_hit(
            session,
            task_id=f"sum-{age}",
            record_id=f"sum-rec-{age}",
            program=program,
            when=at_age(clock, age),
            platform=f"plat{age}",
        )
    add_hit(
        session,
        task_id="sum-unknown",
        record_id="sum-rec-unknown",
        program=program,
        when=at_age(clock, 15),
        created_at=False,
        platform="plat-unknown",
    )
    add_user(session, "sum-regular", clock, "regular")
    add_user(session, "sum-vip", clock, "vip")
    add_user(session, "sum-p90", clock, "program_90", program=program)
    add_user(session, "sum-p30", clock, "program_30", program=program)
    session.commit()

    def fetch(user_id=None):
        headers = client.auth(user_id) if user_id else {}
        response = client.get("/api/v1/programs/search", params={"name": program}, headers=headers)
        assert response.status_code == 200, response.text
        return response.json()

    expected = {
        None: {
            "locked_total": 5,
            "by_required_tier": {"member": 2, "vip": 2, "evidence_only": 1},
            "unlockable_total": 4,
            "cheapest_full_unlock": "single_program_30d",
            "unlocks_by_product": {
                "screenshot": 2,
                "single_program_90d": 2,
                "single_program_30d": 4,
                "membership_regular": 2,
                "membership_vip": 4,
            },
        },
        "sum-regular": {
            "locked_total": 3,
            "by_required_tier": {"member": 0, "vip": 2, "evidence_only": 1},
            "unlockable_total": 2,
            "cheapest_full_unlock": "single_program_30d",
            "unlocks_by_product": {"single_program_30d": 2, "membership_vip": 2},
        },
        "sum-vip": {
            "locked_total": 1,
            "by_required_tier": {"member": 0, "vip": 0, "evidence_only": 1},
            "unlockable_total": 0,
            "cheapest_full_unlock": None,
            "unlocks_by_product": {},
        },
        "sum-p90": {
            "locked_total": 3,
            "by_required_tier": {"member": 0, "vip": 2, "evidence_only": 1},
            "unlockable_total": 2,
            "cheapest_full_unlock": "single_program_30d",
            "unlocks_by_product": {"single_program_30d": 2, "membership_vip": 2},
        },
        "sum-p30": {
            "locked_total": 1,
            "by_required_tier": {"member": 0, "vip": 0, "evidence_only": 1},
            "unlockable_total": 0,
            "cheapest_full_unlock": None,
            "unlocks_by_product": {},
        },
    }
    for user_id, summary in expected.items():
        body = fetch(user_id)
        assert body["locked_summary"] == summary
        tiers = summary["by_required_tier"]
        assert summary["unlockable_total"] == tiers["member"] + tiers["vip"]
        raw = json.dumps(body["locked_records"])
        for age in (10, 40, 50, 100, 120):
            row = by_id(body)[f"sum-rec-{age}"]
            if row["locked"]:
                assert set(row) == LOCKED_KEYS
                assert f"plat{age}" not in json.dumps(row)
        assert "plat-unknown" not in raw
        unknown = by_id(body)["sum-rec-unknown"]
        assert unknown["lock_reason"] == "检测时间待核实"
        assert unknown["age_bucket"] == "unknown"


def test_locked_rows_leak_no_real_fields_and_unconfirmed_stay_hidden(session, client, clock):
    program = "泄漏节目"
    platform = "保密平台甲"
    raw_name = "泄漏节目原名"
    add_hit(
        session,
        task_id="leak-locked",
        record_id="leak-rec",
        program=program,
        when=at_age(clock, 100),
        platform=platform,
    )
    session.add(
        Task(
            task_id="leak-dispute",
            status="done",
            created_at=at_age(clock, 100),
            raw_filename="leak-dispute.mp4",
            network_type="公网",
            platform_name="疑似平台",
            source_unverified=False,
            is_test=False,
        )
    )
    session.add(
        IndexRecord(
            id="leak-dispute-rec",
            task_id="leak-dispute",
            program_name=program,
            program_name_raw=raw_name,
            hit_type="dispute",
            frame_count=2,
            timestamps=["00:09:09"],
        )
    )
    session.commit()
    body = client.get("/api/v1/programs/search", params={"name": program}).json()
    assert body["program_name"] == program
    assert body["outcome"] == "hit"
    assert body["platform_count"] == 1
    assert "leak-dispute-rec" not in json.dumps(body)
    assert "疑似平台" not in json.dumps(body)
    assert body["locked_summary"]["locked_total"] == 1
    assert body["locked_summary"]["by_required_tier"]["member"] == 1
    forbidden_keys = {
        "match_type",
        "alias",
        "pending",
        "hit_type",
        "program_name_raw",
        "platform",
        "platform_operator",
        "platform_province",
        "platform_subtype",
        "thumbnail_url",
        "hd_url",
        "checked_at",
        "timestamps",
        "frame_count",
        "network_type",
    }

    def keys(value):
        if isinstance(value, dict):
            for key, item in value.items():
                yield key
                yield from keys(item)
        elif isinstance(value, list):
            for item in value:
                yield from keys(item)

    assert "match_type" not in set(keys(body))
    assert "pending" not in set(keys(body))
    locked = body["locked_records"]
    assert len(locked) == 1
    row = locked[0]
    assert set(row) == LOCKED_KEYS
    assert forbidden_keys.isdisjoint(row)
    text = json.dumps(row, ensure_ascii=False)
    for secret in (program, platform, raw_name, "leak-dispute.mp4", "00:09:09", "待确认", "疑似"):
        assert secret not in text
    assert "http" not in text
    assert "signed://" not in text


def test_unrecognized_platform_still_counts_as_a_hit(session, client, clock):
    program = "平台节目"
    add_hit(
        session,
        task_id="plat-known",
        record_id="plat-known-rec",
        program=program,
        when=at_age(clock, 200),
        platform="广东移动",
    )
    add_hit(
        session,
        task_id="plat-unknown",
        record_id="plat-unknown-rec",
        program=program,
        when=at_age(clock, 200),
        unverified=True,
    )
    add_hit(
        session,
        task_id="plat-many",
        record_id="plat-many-rec",
        program=program,
        when=at_age(clock, 200),
        unverified=True,
    )
    session.commit()
    body = client.get("/api/v1/programs/search", params={"name": program}).json()
    assert body["outcome"] == "hit"
    assert body["platform_count"] == 1
    assert body["unverified_source_count"] == 2
    rows = by_id(body)
    assert rows["plat-known-rec"]["platform"] == "广东移动"
    assert rows["plat-unknown-rec"]["platform"] == "其他平台"
    assert rows["plat-many-rec"]["platform"] == "其他平台"
    assert "双解平台" not in json.dumps(body)


def _store_platform(session, task_id, record_id, program, when, raw_platform, network="公网"):
    parsed = PlatformNames.normalize(raw_platform)
    session.add(
        Task(
            task_id=task_id,
            status="done",
            created_at=when,
            raw_filename=f"{task_id}.mp4",
            network_type=network,
            platform_name=parsed.name,
            platform_province=parsed.province,
            platform_operator=parsed.operator,
            platform_subtype=parsed.subtype,
            source_unverified=parsed.unrecognized,
            is_test=False,
        )
    )
    session.add(
        IndexRecord(
            id=record_id,
            task_id=task_id,
            program_name=program,
            program_name_raw=program,
            hit_type="consensus",
            frame_count=1,
            timestamps=["00:00:01"],
        )
    )


def test_platform_count_is_province_plus_operator(session, client, clock):
    program = "运营商节目"
    when = at_age(clock, 200)
    _store_platform(session, "op-gd-ott", "op-gd-ott-rec", program, when, "广东移动OTT")
    _store_platform(session, "op-gd-bestv", "op-gd-bestv-rec", program, when, "广东移动百视通")
    _store_platform(session, "op-zj", "op-zj-rec", program, when, "浙江移动", network="专网")
    _store_platform(session, "op-bad", "op-bad-rec", program, when, "没有这个平台")
    session.commit()
    body = client.get("/api/v1/programs/search", params={"name": program}).json()
    assert body["outcome"] == "hit"
    assert body["platform_count"] == 2
    assert body["unverified_source_count"] == 1
    rows = by_id(body)
    assert rows["op-gd-ott-rec"]["platform"] == "广东移动"
    assert rows["op-gd-ott-rec"]["platform_operator"] == "移动"
    assert rows["op-gd-ott-rec"]["platform_province"] == "广东"
    assert rows["op-gd-ott-rec"]["platform_subtype"] == "OTT"
    assert rows["op-gd-bestv-rec"]["platform_subtype"] == "百视通"
    assert rows["op-zj-rec"]["platform"] == "浙江移动"
    assert rows["op-zj-rec"]["network_type"] == "专网"
    assert rows["op-bad-rec"]["platform"] == "其他平台"
    assert "platform_operator" not in rows["op-bad-rec"]


def test_fragment_titles_are_not_billable(session, client, clock):
    when = at_age(clock, 200)
    for task_id, program in (
        ("frag-dog", "狗"),
        ("frag-home", "家庭"),
        ("frag-short", "甲乙"),
        ("frag-ok", "西游记"),
    ):
        add_hit(
            session,
            task_id=task_id,
            record_id=f"{task_id}-rec",
            program=program,
            when=when,
            platform="广东移动",
        )
    session.commit()
    ProgramTitleGuard.set_stop_words(frozenset({"家庭", "狗", "怪", "远方"}))
    try:
        for name in ("狗", "家庭", "怪", "远方"):
            body = client.get("/api/v1/programs/search", params={"name": name}).json()
            assert body["outcome"] == "miss"
            assert body["platform_groups"] == []
            assert body["locked_records"] == []
            assert body["platform_count"] == 0
            assert body["locked_summary"]["locked_total"] == 0
            assert body["locked_summary"]["unlockable_total"] == 0
        short = client.get("/api/v1/programs/search", params={"name": "甲乙"}).json()
        assert short["outcome"] == "hit"
        assert short["platform_groups"][0]["records"][0]["record_id"] == "frag-short-rec"
        found = client.get("/api/v1/programs/search", params={"name": "西游记"}).json()
        assert found["outcome"] == "hit"
        assert found["platform_groups"][0]["records"][0]["record_id"] == "frag-ok-rec"
    finally:
        ProgramTitleGuard.set_stop_words(None)


def test_no_zip_or_download_route(client):
    paths = [getattr(route, "path", "") for route in client.app.routes]
    assert "/api/v1/programs/search" in paths
    assert not any("zip" in path.lower() or "download" in path.lower() for path in paths)


def test_category_only_revealed_on_permitted_records(session, client, clock):
    add_user(session, "category-user", clock, "regular")
    for record_id, age, category in (
        ("cat-open", 100, "共识类别"),
        ("cat-blank", 110, None),
        ("cat-locked", 40, "隐藏类别"),
    ):
        add_hit(
            session,
            task_id=record_id,
            record_id=record_id,
            program="类别节目",
            when=at_age(clock, age),
        )
        session.flush()
        session.get(IndexRecord, record_id).category = category
    session.commit()
    response = client.get(
        "/api/v1/programs/search",
        params={"name": "类别节目"},
        headers=client.auth("category-user"),
    )
    rows = by_id(response.json())
    assert rows["cat-open"]["category"] == "共识类别"
    assert "category" not in rows["cat-blank"]
    assert set(rows["cat-locked"]) == LOCKED_KEYS
    assert "隐藏类别" not in response.text


def test_retired_mappings_cannot_change_search(session, client, clock):
    from nadou.db import Base

    session.execute(
        Base.metadata.tables["program_aliases"]
        .insert()
        .values(alias="历史名称", canonical_name="规范名称")
    )
    session.execute(
        Base.metadata.tables["platform_aliases"]
        .insert()
        .values(alias="广东移动", canonical_name="浙江移动")
    )
    add_hit(
        session,
        task_id="retired-map",
        record_id="retired-map-rec",
        program="规范名称",
        when=at_age(clock, 200),
        platform="广东移动",
    )
    session.commit()
    assert (
        client.get("/api/v1/programs/search", params={"name": "历史名称"}).json()["outcome"]
        == "miss"
    )
    body = client.get("/api/v1/programs/search", params={"name": "规范名称"}).json()
    assert body["platform_groups"][0]["records"][0]["platform"] == "广东移动"
    assert client.get("/api/v1/admin/aliases").status_code == 404


def test_approved_stop_words_never_hit_but_real_short_titles_do(session, client, clock):
    titles = ["怪", "爱情", "家庭", "远方", "狗", "父亲", "母亲", "爱情故事集"]
    for index, title in enumerate(titles):
        add_hit(
            session,
            task_id=f"stop-task-{index}",
            record_id=f"stop-rec-{index}",
            program=title,
            when=at_age(clock, 200),
        )
    session.commit()
    for title in titles:
        body = client.get("/api/v1/programs/search", params={"name": title}).json()
        assert body["outcome"] == ("miss" if title in {"怪", "爱情"} else "hit")
        if title in {"怪", "爱情"}:
            assert body["platform_count"] == 0
            assert body["platform_groups"] == []
            assert body["locked_records"] == []
