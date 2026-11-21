import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from nadou.models import AuditLog, StaffAccount


class AuditWriter:
    """Append one workbench audit row. Callers never update or delete through this class."""

    def __init__(
        self,
        session: Session,
        now: datetime,
        actor: StaffAccount | None,
        ip: str | None,
    ) -> None:
        self.session = session
        self.now = now
        self.actor = actor
        self.ip = ip

    def write(
        self,
        object_type: str,
        object_id: str,
        action: str,
        before: dict | None,
        after: dict | None,
    ) -> AuditLog:
        row = AuditLog(
            actor_id=None if self.actor is None else self.actor.id,
            actor_username=None if self.actor is None else self.actor.username,
            created_at=self.now,
            object_type=object_type,
            object_id=str(object_id),
            action=action,
            before_json=AuditWriter.jsonable(before),
            after_json=AuditWriter.jsonable(after),
            ip=self.ip,
        )
        self.session.add(row)
        self.session.flush()
        return row

    @staticmethod
    def jsonable(value: dict | None) -> dict | None:
        if value is None:
            return None
        return json.loads(json.dumps(value, default=AuditWriter._encode))

    @staticmethod
    def _encode(value: object) -> str:
        if isinstance(value, datetime):
            return value.isoformat()
        raise TypeError(f"audit payload cannot encode {type(value).__name__}")


class AuditView:
    @staticmethod
    def render(row: AuditLog) -> dict:
        return {
            "id": row.id,
            "actor_id": row.actor_id,
            "actor_username": row.actor_username,
            "created_at": row.created_at.isoformat(),
            "object_type": row.object_type,
            "object_id": row.object_id,
            "action": row.action,
            "before": row.before_json,
            "after": row.after_json,
            "ip": row.ip,
        }


class AuditQuery:
    @staticmethod
    def latest(session, limit: int = 100) -> list[dict]:
        rows = session.scalars(
            select(AuditLog).order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(limit)
        ).all()
        return [AuditView.render(row) for row in rows]
