"""A rights holder may release one reviewed W2 row as a display-only summary."""

import argparse
import csv
import hashlib
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from nadou.config import get_settings
from nadou.db import Database
from nadou.filename_parser import FilenameParser
from nadou.importer import TestTaskFilter
from nadou.models import IndexRecord, Task
from nadou.platform_names import PlatformNames
from nadou.text import RecordAge


class SummaryPermission(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    scope: Literal["single_program_summary"]
    review: Literal["pass"]
    rights_holder: str = Field(min_length=1, max_length=100)
    task_id: str = Field(min_length=1, max_length=64)
    program: str = Field(min_length=1, max_length=200)
    platform: str = Field(min_length=1, max_length=100)
    frame_count: int = Field(gt=0)
    w2_sha256: str = Field(pattern="^[a-f0-9]{64}$")
    tasks_sha256: str = Field(pattern="^[a-f0-9]{64}$")
    feedback_sha256: str = Field(pattern="^[a-f0-9]{64}$")


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()


def row_fingerprint(record: IndexRecord, task: Task) -> str:
    # Data inside the digest is never returned. A source re-import or metadata edit
    # invalidates the approval until this exact row has been verified again.
    return digest(
        [
            record.id,
            record.task_id,
            record.program_name,
            record.program_name_raw,
            record.hit_type,
            record.frame_count,
            record.timestamps,
            record.date_generated,
            record.hitlist_sha256,
            task.task_id,
            task.raw_filename,
            task.status,
            task.is_test,
            task.source_unverified,
            task.platform_name,
            task.platform_province,
            task.platform_operator,
            task.network_type,
            task.created_at.astimezone(RecordAge.TZ).isoformat() if task.created_at else None,
            task.approved_hitlist_sha256,
        ]
    )


def approved_summary(record: IndexRecord, task: Task | None) -> bool:
    grant = record.summary_grant
    return (
        isinstance(grant, dict)
        and grant.get("scope") == "single_program_summary"
        and grant.get("review") == "pass"
        and all(
            isinstance(grant.get(key), str) and re.fullmatch("[a-f0-9]{64}", grant[key])
            for key in ("manifest_sha256", "w2_sha256", "tasks_sha256", "feedback_sha256")
        )
        and TestTaskFilter.commercial_task(task)
        and not task.source_unverified
        and task.created_at is not None
        and record.hit_type == "consensus"
        and not record.date_generated
        and grant.get("row_sha256") == row_fingerprint(record, task)
    )


def clocks(values) -> list[str]:
    return [v.strip() for value in values for v in re.split("[,，]", value) if v.strip()]


def file_hash(path: Path) -> str:
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def publish(
    session: Session, permit: SummaryPermission, w2: Path, tasks: Path, feedback_sha256: str
) -> None:
    if (
        permit.feedback_sha256 != feedback_sha256
        or file_hash(w2) != permit.w2_sha256
        or file_hash(tasks) != permit.tasks_sha256
    ):
        raise ValueError("来源文件或人工反馈的摘要不匹配")
    # Only a complete immutable W2 snapshot is a permissible source.
    if any(Path(str(w2) + ext).exists() for ext in ("-wal", "-journal")):
        raise ValueError("W2 必须是完整的只读快照")
    source = sqlite3.connect(w2.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
    try:
        rows = source.execute(
            "SELECT frames,timestamps,created_at,filename,status,hit_type FROM program_index "
            "WHERE task_id=? AND program_name=?",
            (permit.task_id, permit.program),
        ).fetchall()
    finally:
        source.close()
    with tasks.open(encoding="utf-8-sig", newline="") as f:
        manifest_rows = [r for r in csv.DictReader(f) if r["task_id"] == permit.task_id]
    if len(rows) != 1 or len(manifest_rows) != 1:
        raise ValueError("原始来源必须各有唯一匹配项")
    count, raw_times, created, filename, status, kind = rows[0]
    meta = manifest_rows[0]
    if not re.fullmatch(r"[1-9][0-9]*", str(count)):
        raise ValueError("来源帧数不是正整数")
    count = int(count)
    source_times = clocks([raw_times])
    if (
        status != "done"
        or kind != "consensus"
        or meta["状态"] != "done"
        or count != permit.frame_count
        or not filename
        or meta["filename"] != filename
        or len(source_times) != count
        or len(set(source_times)) != count
        or not all(re.fullmatch(r"(?:\d+:)?[0-5]?\d:[0-5]\d(?:\.\d+)?", t) for t in source_times)
    ):
        raise ValueError("共识、帧数、时间点或任务元数据不一致")
    declared = RecordAge.parse_submitted(meta["提交时间"])
    clock = datetime.fromisoformat(created)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=RecordAge.TZ)
    parsed = FilenameParser.parse(filename)
    platform = PlatformNames.normalize(meta["平台名"] or parsed.platform)
    network = meta["公网/专网"] or parsed.network_type
    if (
        clock.replace(microsecond=0) != declared
        or platform.unrecognized
        or platform.name != permit.platform
        or network not in ("公网", "专网")
    ):
        raise ValueError("来源日期、网络或平台与授权不匹配")
    task = session.scalar(select(Task).where(Task.task_id == permit.task_id).with_for_update())
    record = session.scalar(
        select(IndexRecord)
        .where(IndexRecord.task_id == permit.task_id, IndexRecord.program_name == permit.program)
        .with_for_update()
    )
    if (
        not TestTaskFilter.commercial_task(task)
        or task.source_unverified
        or not task.created_at
        or task.created_at.replace(microsecond=0) != declared
        or task.raw_filename != filename
        or task.network_type != network
        or task.platform_name != platform.name
        or task.platform_operator != platform.operator
        or task.platform_province != platform.province
        or not record
        or record.hit_type != "consensus"
        or record.date_generated
        or record.frame_count != count
        or clocks(record.timestamps) != source_times
    ):
        raise ValueError("项目索引与指定来源不一致")
    if task.approved_hitlist_sha256 and record.hitlist_sha256 == task.approved_hitlist_sha256:
        raise ValueError("该记录已有整表授权，请先核对权限范围")
    if file_hash(w2) != permit.w2_sha256 or file_hash(tasks) != permit.tasks_sha256:
        raise ValueError("核验期间来源已变更")
    record.summary_grant = {
        "scope": permit.scope,
        "review": permit.review,
        "manifest_sha256": digest(permit.model_dump()),
        "feedback_sha256": permit.feedback_sha256,
        "w2_sha256": permit.w2_sha256,
        "tasks_sha256": permit.tasks_sha256,
        "row_sha256": row_fingerprint(record, task),
    }


def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="publish-reviewed-summary")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--w2", type=Path, required=True)
    parser.add_argument("--task-csv", type=Path, required=True)
    parser.add_argument(
        "--feedback-sha256", required=True, help="运维人员对已核实的原始人工反馈文件实算的摘要"
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    permission = SummaryPermission.model_validate_json(args.manifest.read_bytes())
    with Database(get_settings().database_url).session() as session:
        publish(session, permission, args.w2, args.task_csv, args.feedback_sha256)
        if args.apply:
            session.commit()
        else:
            session.rollback()
    print("已提交一条展示摘要" if args.apply else "单行来源与授权核验通过，未提交")
    return 0
