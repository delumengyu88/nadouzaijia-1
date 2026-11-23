import json
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from nadou.admin.indexing import IndexStatus
from nadou.config import get_settings
from nadou.models import Entitlement, IndexRecord, Task, User, W2IndexImport
from nadou.records import IndexRecordStore
from nadou.search import ProgramSearch
from nadou.w2_import import ExcludeList, W2Importer, W2ImportError, import_release, run
from tests.w2_helpers import build_release


@pytest.fixture(autouse=True)
def production_rules(monkeypatch):
    # Visibility must follow the production approval switch, not demo shortcuts.
    monkeypatch.setenv("NADOU_DATA_SOURCE", "export")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def stamp(clock, days):
    return (clock - timedelta(days=days)).replace(tzinfo=None).isoformat()


def old_rows(clock):
    when = stamp(clock, 60)
    recent = stamp(clock, 5)
    return [
        (
            "三毛流浪记",
            "t-old",
            "consensus",
            "5",
            "00:01:01, 00:01:02",
            when,
            "公网-示例视频-a.mp4",
            "done",
        ),
        ("三毛流浪记", "t-old", "dispute", "3", "00:09:09", when, "公网-示例视频-a.mp4", "done"),
        ("蜡笔小新", "t-old", "dispute", "4", "00:02:00", when, "公网-示例视频-a.mp4", "done"),
        ("怪", "t-old", "consensus", "9", "00:03:00", when, "公网-示例视频-a.mp4", "done"),
        ("家庭", "t-old", "consensus", "2", "00:04:00", when, "公网-示例视频-a.mp4", "done"),
        ("向日葵", "t-new", "consensus", "2", "00:05:00", recent, "专网-示例视频-b.mp4", "done"),
        ("向日葵", "t-meta", "consensus", "2", "00:05:00", None, None, None),
    ]


def factory(session):
    session.commit()
    return sessionmaker(bind=session.get_bind(), expire_on_commit=False)


def records(session):
    session.expire_all()
    return {(r.task_id, r.program_name): r for r in session.scalars(select(IndexRecord))}


def shown(session, key):
    session.expire_all()
    record = session.scalar(
        select(IndexRecord).where(IndexRecord.task_id == key[0], IndexRecord.program_name == key[1])
    )
    return ProgramSearch.visible(record, session.get(Task, record.task_id))


def test_first_import_applies_display_rules(session, clock, tmp_path):
    source = build_release(tmp_path / "v1", old_rows(clock))
    stats, release = import_release(factory(session), source, clock)

    rows = records(session)
    assert ("t-old", "怪") not in rows  # stop word dropped entirely
    assert rows[("t-old", "家庭")].hit_type == "review"  # low confidence kept hidden
    sanmao = rows[("t-old", "三毛流浪记")]
    assert sanmao.hit_type == "consensus" and sanmao.frame_count == 5
    assert sanmao.timestamps == ["00:01:01", "00:01:02"]
    assert sanmao.id == IndexRecordStore.public_id("t-old", "三毛流浪记")
    assert shown(session, ("t-old", "三毛流浪记"))
    assert not shown(session, ("t-old", "蜡笔小新"))  # dispute is internal
    assert not shown(session, ("t-old", "家庭"))
    assert shown(session, ("t-new", "向日葵"))
    # A task with no metadata anywhere is skipped until the task CSV knows it.
    assert ("t-meta", "向日葵") not in rows
    assert stats.skipped_rows == 1 and stats.new_tasks == 2 and stats.excluded == 1
    task = session.get(Task, "t-old")
    assert task.platform_name and task.network_type == "公网"

    log = session.scalar(select(W2IndexImport))
    assert log.status == "success" and log.version == "20261121_041700"
    assert log.consensus_names == release.consensus_names == 3
    assert log.hit_rows == 7 and log.kept_consensus_names == 4
    payload = IndexStatus.payload(session, clock)["w2_index"]
    assert payload["current"]["version"] == "20261121_041700"
    assert payload["current"]["consensus_names"] == 3


def test_reimport_is_idempotent_and_keeps_ids(session, clock, tmp_path):
    source = build_release(tmp_path / "v1", old_rows(clock))
    make = factory(session)
    import_release(make, source, clock)
    before = {k: r.id for k, r in records(session).items()}

    stats, _ = import_release(make, source, clock)
    assert stats.skipped
    stats, _ = import_release(make, source, clock, force=True)
    assert stats.inserted == 0 and stats.updated == 0
    assert {k: r.id for k, r in records(session).items()} == before
    assert session.scalar(select(W2IndexImport).where(W2IndexImport.id == 2)) is not None


def test_existing_task_metadata_wins_and_user_data_survives(session, clock, tmp_path):
    session.add(
        Task(
            task_id="t-old",
            status="done",
            created_at=clock - timedelta(days=60),
            raw_filename="csv-name.mp4",
            network_type="专网",
            platform_name="CSV平台",
            source_unverified=False,
            is_test=False,
        )
    )
    session.add(User(id="u1", created_at=clock))
    record_id = IndexRecordStore.public_id("t-old", "三毛流浪记")
    session.add(
        Entitlement(
            user_id="u1",
            kind="program_90",
            program_name="三毛流浪记",
            record_id=record_id,
            expires_at=clock + timedelta(days=30),
        )
    )
    make = factory(session)
    import_release(make, build_release(tmp_path / "v1", old_rows(clock)), clock)
    task = session.get(Task, "t-old")
    session.refresh(task)
    assert task.platform_name == "CSV平台" and task.network_type == "专网"

    # Next release no longer has the old title: it is hidden, never deleted.
    rows = [r for r in old_rows(clock) if r[0] != "三毛流浪记"]
    import_release(make, build_release(tmp_path / "v2", rows, version="20261122_041700"), clock)
    assert session.get(IndexRecord, record_id) is not None
    assert not shown(session, ("t-old", "三毛流浪记"))
    assert session.scalar(select(Entitlement).where(Entitlement.record_id == record_id))


def test_recent_rows_keep_display_state_across_rebuilds(session, clock, tmp_path):
    make = factory(session)
    import_release(make, build_release(tmp_path / "v1", old_rows(clock)), clock)
    assert shown(session, ("t-new", "向日葵"))

    # Rebuild flips the recent row to dispute and drops nothing else.
    rows = [(*r[:2], "dispute", *r[3:]) if r[1] == "t-new" else r for r in old_rows(clock)]
    stats, _ = import_release(
        make, build_release(tmp_path / "v2", rows, version="20261122_041700"), clock
    )
    assert stats.frozen == 1
    assert shown(session, ("t-new", "向日葵"))
    assert records(session)[("t-new", "向日葵")].hit_type == "consensus"

    # A rebuild that omits the recent row carries it forward.
    rows = [r for r in old_rows(clock) if r[1] != "t-new"]
    stats, _ = import_release(
        make, build_release(tmp_path / "v3", rows, version="20261123_041700"), clock
    )
    assert stats.carried == 1 and stats.retired_tasks == 1
    assert shown(session, ("t-new", "向日葵"))

    # Old rows do follow the rebuild.
    rows = [(*r[:2], "dispute", *r[3:]) if r[0] == "三毛流浪记" else r for r in rows]
    rows = [r for r in rows if r[2] != "consensus" or r[0] != "三毛流浪记"]
    import_release(make, build_release(tmp_path / "v4", rows, version="20261124_041700"), clock)
    assert not shown(session, ("t-old", "三毛流浪记"))


def test_first_import_does_not_freeze_rows_from_other_sources(session, clock):
    session.add(
        Task(
            task_id="t-new",
            status="done",
            created_at=clock - timedelta(days=3),
            raw_filename="x.mp4",
            source_unverified=False,
            is_test=False,
        )
    )
    IndexRecordStore(session).upsert(
        task_id="t-new",
        program_name="向日葵",
        program_name_raw="向日葵",
        hit_type="dispute",
        frame_count=1,
        timestamps=[],
    )
    session.commit()
    assert not shown(session, ("t-new", "向日葵"))


def test_first_import_updates_recent_non_w2_rows(session, clock, tmp_path):
    test_first_import_does_not_freeze_rows_from_other_sources(session, clock)
    stats, _ = import_release(
        factory(session), build_release(tmp_path / "v1", old_rows(clock)), clock
    )
    assert stats.frozen == 0
    assert shown(session, ("t-new", "向日葵"))


def test_failure_keeps_previous_data(session, clock, tmp_path, monkeypatch):
    make = factory(session)
    import_release(make, build_release(tmp_path / "v1", old_rows(clock)), clock)
    before = {k: (r.hitlist_sha256, r.hit_type) for k, r in records(session).items()}

    bad = build_release(tmp_path / "v2", old_rows(clock)[:3], version="20261122_041700")
    manifest = json.loads((bad / "manifest.json").read_text("utf-8"))
    manifest["md5"]["program_index.db"] = "0" * 32
    (bad / "manifest.json").write_text(json.dumps(manifest), "utf-8")
    with pytest.raises(W2ImportError, match="MD5"):
        import_release(make, bad, clock)

    # An error halfway through the database work rolls the whole import back.
    good = build_release(tmp_path / "v3", old_rows(clock)[:3], version="20261123_041700")

    def boom(self, *args, **kwargs):
        raise RuntimeError("磁盘已满")

    monkeypatch.setattr(W2Importer, "_ensure_tasks", boom)
    with pytest.raises(W2ImportError, match="磁盘已满"):
        import_release(make, good, clock)

    after = {k: (r.hitlist_sha256, r.hit_type) for k, r in records(session).items()}
    assert after == before
    statuses = [r.status for r in session.scalars(select(W2IndexImport).order_by("id"))]
    assert statuses == ["success", "failed", "failed"]
    assert W2Importer(session, clock).current().version == "20261121_041700"


def test_count_mismatch_is_rejected(session, clock, tmp_path):
    source = build_release(tmp_path / "v1", old_rows(clock), manifest_overrides={"hit_rows": 99})
    with pytest.raises(W2ImportError, match="hit_rows"):
        import_release(factory(session), source, clock)
    source = build_release(tmp_path / "v2", old_rows(clock), drop_columns=("frames",))
    with pytest.raises(W2ImportError, match="缺列"):
        import_release(factory(session), source, clock)


def test_summary_grant_tasks_are_left_alone(session, clock, tmp_path):
    session.add(
        Task(
            task_id="t-old",
            status="done",
            created_at=clock - timedelta(days=60),
            raw_filename="a.mp4",
            approved_hitlist_sha256="a" * 64,
            source_unverified=False,
            is_test=False,
        )
    )
    record = IndexRecordStore(session).upsert(
        task_id="t-old",
        program_name="三毛流浪记",
        program_name_raw="三毛流浪记",
        hit_type="consensus",
        frame_count=1,
        timestamps=[],
    )
    record.summary_grant = {"scope": "single_program_summary"}
    stats, _ = import_release(
        factory(session), build_release(tmp_path / "v1", old_rows(clock)), clock
    )
    assert stats.protected_tasks == 1
    assert session.get(Task, "t-old").approved_hitlist_sha256 == "a" * 64
    assert ("t-old", "蜡笔小新") not in records(session)


def test_verified_excludes_apply_only_to_old_rows(session, clock, tmp_path):
    source = build_release(tmp_path / "v1", old_rows(clock), verified=("三毛流浪记", "向日葵"))
    import_release(factory(session), source, clock)
    rows = records(session)
    assert ("t-old", "三毛流浪记") not in rows
    assert ("t-new", "向日葵") in rows


def test_exclude_list_shapes():
    stop, low, verified = ExcludeList.parse(
        {"stopwords": ["怪"], "low_confidence": ["狗"], "verified_excludes": ["X"], "note": "n"}
    )
    assert stop == {"怪"} and low == {"狗"} and verified == {"x"}
    stop, low, _ = ExcludeList.parse(["怪", {"name": "狗", "type": "低置信"}])
    assert stop == {"怪"} and low == {"狗"}
    with pytest.raises(W2ImportError):
        ExcludeList.parse("怪")


def test_cli_status_and_import(session, clock, tmp_path, monkeypatch, capsys):
    session.commit()
    monkeypatch.setenv("DATABASE_URL", str(session.get_bind().url))
    get_settings.cache_clear()
    source = build_release(tmp_path / "v1", old_rows(clock))
    assert run(["--source", str(source)]) == 0
    assert "导入完成" in capsys.readouterr().out
    assert run(["--source", str(source)]) == 0
    assert "无需导入" in capsys.readouterr().out
    assert run(["--status"]) == 0
    assert '"version": "20261121_041700"' in capsys.readouterr().out
    assert run(["--source", str(tmp_path / "missing")]) == 1
    assert "线上继续使用上一版数据" in capsys.readouterr().out
