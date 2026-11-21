import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from nadou.models import (
    Announcement,
    AuditLog,
    Entitlement,
    EvidenceNote,
    EvidenceRequest,
    HomepageExample,
    InviteCode,
    Order,
    SplashConfig,
    StaffAccount,
    User,
)


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
            "before_text": AuditView.change_text(row.before_json),
            "after_text": AuditView.change_text(row.after_json),
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
        if "ban_type" in merged or "ban_reason" in merged:
            return AuditView.change_text(merged)
        title = merged.get("title") or merged.get("program_name") or merged.get("slogan")
        if title:
            return str(title)
        return ""

    @staticmethod
    def business_label(session: Session, row: AuditLog) -> str:
        found = AuditView.label(row)
        if found:
            return found
        kind = row.object_type
        ident = row.object_id
        if kind == "evidence_request":
            item = session.get(EvidenceRequest, ident)
            if item is not None:
                return item.application_number
        elif kind == "evidence_note" and ident.isdigit():
            note = session.get(EvidenceNote, int(ident))
            if note is not None:
                item = session.get(EvidenceRequest, note.request_id)
                if item is not None:
                    return item.application_number
        elif kind == "order":
            item = session.get(Order, ident)
            if item is not None:
                return item.order_number
        elif kind == "entitlement" and ident.isdigit():
            grant = session.get(Entitlement, int(ident))
            if grant is not None:
                return AuditView._person(session.get(User, grant.user_id))
        elif kind == "user":
            return AuditView._person(session.get(User, ident))
        elif kind == "staff_account":
            staff = session.get(StaffAccount, ident)
            if staff is not None:
                return staff.username
        elif kind == "invite_code":
            code = session.get(InviteCode, ident)
            if code is not None:
                return code.code
        elif kind == "announcement":
            note = session.get(Announcement, ident)
            if note is not None:
                return note.title
        elif kind == "homepage_example":
            example = session.get(HomepageExample, ident)
            if example is not None:
                return example.program_name
        elif kind == "splash" and ident.isdigit():
            splash = session.get(SplashConfig, int(ident))
            if splash is not None and splash.slogan:
                return splash.slogan
            return "开屏"
        return "记录"

    @staticmethod
    def change_text(payload) -> str:
        if not isinstance(payload, dict) or not payload:
            return "—"
        if "ban_type" in payload or "ban_reason" in payload:
            if payload.get("ban_type") == "permanent":
                return f"状态 已封禁；期限 永久；原因 {payload.get('ban_reason') or ''}"
            if payload.get("ban_type") == "temporary":
                return f"状态 已封禁；期限 临时；原因 {payload.get('ban_reason') or ''}"
            return "状态 已解禁"
        labels = {
            "status": "状态",
            "tier": "档位",
            "duration_days": "天数",
            "reason": "原因",
            "days": "天数",
            "channel": "渠道",
            "count": "数量",
            "code": "邀请码",
            "text": "内容",
            "username": "账号",
            "role": "角色",
            "application_number": "申请号",
            "quote_amount": "报价",
            "payment_amount": "收款",
            "delivery_method": "交付方式",
            "delivery_note": "说明",
            "failure_reason": "失败原因",
            "refund_method": "退款方式",
            "refund_amount": "退款金额",
            "refund_note": "退款记录",
            "attempts": "尝试次数",
            "new_rows": "新增行数",
            "error": "说明",
            "title": "标题",
            "program_name": "节目",
            "sort_order": "排序",
            "amount": "金额",
        }
        parts = []
        for key, label in labels.items():
            if key not in payload or isinstance(payload[key], (dict, list)):
                continue
            item = payload[key]
            if item is None:
                continue
            if key == "role":
                item = "管理员" if item == "admin" else "客服"
            elif key == "tier":
                item = {"vip": "VIP", "regular": "普通会员"}.get(item, item)
            elif isinstance(item, bool):
                item = "是" if item else "否"
            parts.append(f"{label} {item}")
        return "；".join(parts) if parts else "—"

    @staticmethod
    def _person(user: User | None) -> str:
        if user is None:
            return "记录"
        name = user.display_name or "用户"
        phone = user.phone or ""
        if len(phone) >= 4:
            return f"{name} {phone[-4:]}"
        return name


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
        created_before: datetime | None = None,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        statement = select(AuditLog).order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        if actor:
            statement = statement.where(AuditLog.actor_username == actor)
        if object_type:
            statement = statement.where(AuditLog.object_type == object_type)
        if created_from is not None:
            statement = statement.where(AuditLog.created_at >= created_from)
        if created_to is not None:
            statement = statement.where(AuditLog.created_at <= created_to)
        if created_before is not None:
            statement = statement.where(AuditLog.created_at < created_before)
        rows = list(session.scalars(statement.limit(500)).all())
        needle = (query or "").strip().casefold()
        if needle:
            rows = [row for row in rows if needle in AuditQuery._blob(row)]
        shown = []
        for row in rows[offset : offset + limit]:
            body = AuditView.render(row)
            body["object_label"] = AuditView.business_label(session, row)
            shown.append(body)
        return shown, len(rows)

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
