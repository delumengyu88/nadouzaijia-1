from datetime import UTC

import pytest
from sqlalchemy import select

from nadou.importer import TaskExportImporter, TaskExportImportError
from nadou.models import PlatformAlias, Task
from nadou.text import RecordAge

HEADER = "task_id,状态,提交时间,filename,公网/专网,平台名"


def _import(session, body: str):
    return TaskExportImporter(session).import_csv(f"{HEADER}\n{body}\n")


def test_example_row_and_beijing_time(session):
    result = _import(
        session,
        "a1b2c3d4,done,2026-05-18 11:12:24,公网-示例视频-电影-2026.05.18.mp4,公网,示例视频",
    )
    assert result.inserted == 1
    task = session.get(Task, "a1b2c3d4")
    assert task.status == "done"
    assert task.raw_filename == "公网-示例视频-电影-2026.05.18.mp4"
    assert task.network_type == "公网"
    assert task.platform_name == "示例视频"
    assert task.source_unverified is False
    local = task.created_at.astimezone(RecordAge.TZ)
    assert (local.year, local.month, local.day, local.hour, local.minute, local.second) == (
        2026,
        5,
        18,
        11,
        12,
        24,
    )
    assert task.created_at.astimezone(UTC).hour == 3


def test_reimport_updates_in_place(session):
    _import(
        session,
        "a1b2c3d4,done,2026-05-18 11:12:24,公网-示例视频-电影-2026.05.18.mp4,公网,示例视频",
    )
    second = _import(
        session,
        "a1b2c3d4,cancelled,2026-06-01 08:00:00,renamed.mp4,专网,另一平台",
    )
    assert second.inserted == 0
    assert second.updated == 1
    tasks = session.scalars(select(Task)).all()
    assert len(tasks) == 1
    assert tasks[0].status == "cancelled"
    assert tasks[0].raw_filename == "renamed.mp4"
    assert tasks[0].network_type == "专网"
    assert tasks[0].platform_name == "另一平台"


def test_empty_columns_fall_back_to_filename(session):
    _import(session, "t-parse,done,2026-01-02 03:04:05,公网-解析平台-电影-2026.01.02.mp4,,")
    task = session.get(Task, "t-parse")
    assert task.network_type == "公网"
    assert task.platform_name == "解析平台"
    assert task.source_unverified is False


def test_provided_columns_win_over_filename(session):
    _import(
        session,
        "t-col,done,2026-01-02 03:04:05,公网-解析平台-电影-2026.01.02.mp4,专网,指定平台",
    )
    task = session.get(Task, "t-col")
    assert task.network_type == "专网"
    assert task.platform_name == "指定平台"


def test_invalid_network_does_not_fall_back(session):
    _import(
        session,
        "t-badnet,done,2026-01-02 03:04:05,专网-解析平台-电影-2026.01.02.mp4,其他,",
    )
    task = session.get(Task, "t-badnet")
    assert task.network_type is None
    assert task.platform_name == "解析平台"


def test_unknown_filename_is_unverified(session):
    _import(session, "t-unk,done,2026-01-02 03:04:05,unknown.mp4,,")
    _import(session, "t-vid,done,2026-01-02 03:04:05,VID_20260102.mp4,,")
    for task_id in ("t-unk", "t-vid"):
        task = session.get(Task, task_id)
        assert task.network_type is None
        assert task.platform_name is None
        assert task.source_unverified is True


def test_alias_merges_column_and_parser_output(session):
    session.add_all(
        [
            PlatformAlias(alias="重庆移动百视通OTT", canonical_name="重庆移动"),
            PlatformAlias(alias="重庆移动百视通", canonical_name="重庆移动"),
        ]
    )
    session.flush()
    _import(
        session,
        "\n".join(
            [
                "t-col,done,2026-01-02 03:04:05,unknown.mp4,公网,重庆移动百视通OTT",
                "t-parse,done,2026-01-02 03:04:05,公网-重庆移动百视通-电影-2026.01.02.mp4,,",
                "t-wide,done,2026-01-02 03:04:05,unknown.mp4,专网,重庆移动百视通ＯＴＴ",
            ]
        ),
    )
    for task_id in ("t-col", "t-parse", "t-wide"):
        task = session.get(Task, task_id)
        assert task.platform_name == "重庆移动"
        assert task.source_unverified is False


def test_test_prefix_is_flagged_even_when_done(session):
    result = _import(
        session,
        "\n".join(
            [
                "t-file,done,2026-01-02 03:04:05,TEST_demo.mp4,公网,示例视频",
                "t-plat,done,2026-01-02 03:04:05,公网-示例视频-电影-2026.01.02.mp4,公网,Test_平台",
                "t-mid,done,2026-01-02 03:04:05,notes_test_a.mp4,公网,示例视频",
                "t-wide,done,2026-01-02 03:04:05,ｔｅｓｔ＿wide.mp4,公网,示例视频",
            ]
        ),
    )
    assert result.test_rows == 3
    assert session.get(Task, "t-file").is_test is True
    assert session.get(Task, "t-plat").is_test is True
    assert session.get(Task, "t-wide").is_test is True
    assert session.get(Task, "t-mid").is_test is False
    assert session.get(Task, "t-file").status == "done"


def test_reimport_clears_test_flag(session):
    _import(session, "t-file,done,2026-01-02 03:04:05,test_demo.mp4,公网,示例视频")
    assert session.get(Task, "t-file").is_test is True
    _import(
        session, "t-file,done,2026-01-02 03:04:05,公网-示例视频-电影-2026.01.02.mp4,公网,示例视频"
    )
    assert session.get(Task, "t-file").is_test is False


def test_rejects_bad_csv(session):
    with pytest.raises(TaskExportImportError):
        TaskExportImporter(session).import_csv("task_id,状态\n")
    with pytest.raises(TaskExportImportError):
        _import(session, "t-x,nope,2026-01-02 03:04:05,a.mp4,,")
    with pytest.raises(TaskExportImportError):
        _import(session, "t-x,done,not-a-time,a.mp4,,")
