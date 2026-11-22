import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from nadou.models import IndexRecord
from nadou.normalize import ProgramNormalizer

_NAMESPACE = uuid.UUID("6f9c0b2e-1c0a-4f3e-9a77-3a1e0a9b7c21")
_HIT_TYPES = frozenset({"consensus", "dispute"})


class IndexRecordStore:
    """One index row per task plus canonical program name. Re-upsert updates it."""

    def __init__(self, session: Session) -> None:
        self.session = session

    @staticmethod
    def public_id(task_id: str, program_name: str) -> str:
        return str(uuid.uuid5(_NAMESPACE, f"{task_id}:{program_name}"))

    def upsert(
        self,
        *,
        task_id: str,
        program_name: str,
        program_name_raw: str,
        hit_type: str,
        frame_count: int,
        timestamps: list[str],
        uploaded_at: datetime | None = None,
        record_id: str | None = None,
        category: str | None = None,
    ) -> IndexRecord:
        if hit_type not in _HIT_TYPES:
            raise ValueError(f"unknown hit_type {hit_type!r}")
        if frame_count < 0:
            raise ValueError("frame_count must be >= 0")
        canonical = ProgramNormalizer().canonical(program_name)
        if not canonical:
            raise ValueError("program_name is empty")
        existing = self.session.scalar(
            select(IndexRecord).where(
                IndexRecord.task_id == task_id,
                IndexRecord.program_name == canonical,
            )
        )
        if existing is None:
            existing = IndexRecord(
                id=record_id or self.public_id(task_id, canonical),
                task_id=task_id,
                program_name=canonical,
                program_name_raw=program_name_raw,
                hit_type=hit_type,
                frame_count=frame_count,
                timestamps=list(timestamps),
                uploaded_at=uploaded_at,
                category=category,
            )
            self.session.add(existing)
        else:
            existing.program_name_raw = program_name_raw
            existing.hit_type = hit_type
            existing.frame_count = frame_count
            existing.timestamps = list(timestamps)
            existing.category = category
            if uploaded_at is not None:
                existing.uploaded_at = uploaded_at
        return existing
