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
            "object_label": AuditView.label(row),
            "action": row.action,
            "before": row.before_json,
            "after": row.after_json,
            "ip": row.ip,
        }

    @staticmethod
    def label(row: AuditLog) -> str:
        payload = row.after_json if isinstance(row.after_json, dict) else {}
        earlier = row.before_json if isinstance(row.before_json, dict) else {}
        merged = {**earlier, **payload}
        if merged.get("application_number"):
            return str(merged["application_number"])
        if merged.get("order_number"):
            return str(merged["order_number"])
        if merged.get("code"):
            return str(merged["code"])
        name = merged.get("display_name") or merged.get("username")
        phone = merged.get("phone")
        if name and isinstance(phone, str) and len(phone) >= 4:
            return f"{name} {phone[-4:]}"
        if name:
            return str(name)
        return "记录"


class AuditQuery:
    @staticmethod
    def latest(
        session,
        limit: int = 100,
        *,
        actor: str | None = None,
        object_type: str | None = None,
        query: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
    ) -> list[dict]:
        statement = select(AuditLog).order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        if actor:
            statement = statement.where(AuditLog.actor_username == actor)
        if object_type:
            statement = statement.where(AuditLog.object_type == object_type)
        if created_from is not None:
            statement = statement.where(AuditLog.created_at >= created_from)
        if created_to is not None:
            statement = statement.where(AuditLog.created_at <= created_to)
        rows = list(session.scalars(statement.limit(500)).all())
        needle = (query or "").strip().casefold()
        if needle:
            rows = [row for row in rows if needle in AuditQuery._blob(row)]
        return [AuditView.render(row) for row in rows[:limit]]

    @staticmethod
    def _blob(row: AuditLog) -> str:
        parts = [
            row.actor_username or "",
            row.object_type,
            row.object_id,
            row.action,
            "" if row.before_json is None else str(row.before_json),
            "" if row.after_json is None else str(row.after_json),
        ]
        return " ".join(parts).casefold()
