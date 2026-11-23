"""Record or withdraw an operator-verified permission. The workbench cannot grant it."""

import argparse
from datetime import datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from nadou.config import get_settings
from nadou.db import Database
from nadou.models import IndexRecord, PublicExamplePermit, Task
from nadou.public_home import PublicHome
from nadou.summary_source import file_hash
from nadou.text import RecordAge


class PublicPermission(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    scope: Literal["anonymous_home_example_detail"]
    record_id: str = Field(min_length=1, max_length=36)
    task_id: str = Field(min_length=1, max_length=64)
    program: str = Field(min_length=1, max_length=200)
    source_sha256: str = Field(pattern="^[a-f0-9]{64}$")
    permission_sha256: str = Field(pattern="^[a-f0-9]{64}$")


def grant(session, permission: PublicPermission, evidence: Path, now: datetime) -> None:
    if get_settings().demo_data:
        raise ValueError("公开许可仅可绑定正式项目的已核来源")
    if (
        not evidence.is_file()
        or not evidence.stat().st_size
        or file_hash(evidence) != permission.permission_sha256
    ):
        raise ValueError("权利方匿名公开授权文件不匹配")
    task = session.scalar(select(Task).where(Task.task_id == permission.task_id).with_for_update())
    record = session.scalar(
        select(IndexRecord)
        .where(
            IndexRecord.id == permission.record_id,
            IndexRecord.task_id == permission.task_id,
            IndexRecord.program_name == permission.program,
        )
        .with_for_update()
    )
    if (
        record is None
        or task is None
        or task.source_unverified
        or record.hitlist_sha256 != permission.source_sha256
        or not PublicHome.verified_public_source(record, task, now)
    ):
        raise ValueError("该行来源、公开日期或内容范围不符")
    row_hash = PublicHome.public_row_hash(record, task)
    existing = session.get(PublicExamplePermit, record.id, with_for_update=True)
    if (
        existing
        and existing.active
        and existing.row_sha256 == row_hash
        and existing.source_sha256 == permission.source_sha256
        and existing.permission_sha256 == permission.permission_sha256
    ):
        return
    session.merge(
        PublicExamplePermit(
            record_id=record.id,
            source_sha256=permission.source_sha256,
            row_sha256=row_hash,
            permission_sha256=permission.permission_sha256,
            active=True,
            publication_token=str(uuid4()),
        )
    )


def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="public-example-permit")
    operation = parser.add_mutually_exclusive_group(required=True)
    operation.add_argument("--manifest", type=Path)
    operation.add_argument("--revoke-record")
    parser.add_argument("--permission-file", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    if bool(args.manifest) != bool(args.permission_file):
        parser.error("--manifest 需与 --permission-file 配对使用")
    with Database(get_settings().database_url).session() as session:
        if args.manifest:
            permit = PublicPermission.model_validate_json(args.manifest.read_bytes())
            grant(session, permit, args.permission_file, datetime.now(RecordAge.TZ))
        else:
            existing = session.get(PublicExamplePermit, args.revoke_record, with_for_update=True)
            if existing is None:
                raise ValueError("未找到该记录的公开许可")
            existing.active = False
        if args.apply:
            session.commit()
        else:
            session.rollback()
    print("单条公开许可已处理" if args.apply else "检查通过，未提交变更")
    return 0
