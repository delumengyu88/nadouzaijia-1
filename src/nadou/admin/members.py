from datetime import datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from nadou.admin.audit import AuditWriter
from nadou.admin.errors import WorkbenchError
from nadou.admin.phone import OpenIdMask, PhoneMask
from nadou.models import Entitlement, InviteCode, User


class EntitlementBook:
    """`manual` entitlements are excluded from invite-code conversion and order revenue."""

    MANUAL = "manual"
    ORDER = "order"
    INVITE = "invite"

    @staticmethod
    def counts_for_revenue(source: str | None) -> bool:
        return source in {None, EntitlementBook.ORDER}

    @staticmethod
    def revenue_entitlements(session: Session) -> list[Entitlement]:
        return list(
            session.scalars(
                select(Entitlement).where(
                    or_(
                        Entitlement.source.is_(None),
                        Entitlement.source == EntitlementBook.ORDER,
                    )
                )
            ).all()
        )


class UserView:
    @staticmethod
    def render(user: User, *, reveal_phone: bool) -> dict:
        phone = user.phone if reveal_phone else PhoneMask.apply(user.phone)
        openid = user.wechat_openid if reveal_phone else OpenIdMask.apply(user.wechat_openid)
        return {
            "id": user.id,
            "phone": phone,
            "wechat_openid": openid,
            "display_name": user.display_name,
            "created_at": user.created_at.isoformat(),
            "ban_type": user.ban_type,
            "ban_reason": user.ban_reason,
            "ban_until": user.ban_until.isoformat() if user.ban_until else None,
            "banned_at": user.banned_at.isoformat() if user.banned_at else None,
        }


class MemberDirectory:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, user_id: str) -> User:
        user = self.session.get(User, user_id)
        if user is None:
            raise WorkbenchError(404, "user not found")
        return user

    def by_phone(self, phone: str) -> list[User]:
        return self.by_contact(phone)

    def by_contact(self, query: str) -> list[User]:
        text = query.strip()
        if not text:
            raise WorkbenchError(400, "phone is required")
        return list(
            self.session.scalars(
                select(User).where(or_(User.phone == text, User.wechat_openid == text))
            ).all()
        )


class MemberAdmin:
    TIERS = frozenset({"regular", "vip"})

    def __init__(self, session: Session, audit: AuditWriter) -> None:
        self.session = session
        self.audit = audit

    def ban(
        self,
        user_id: str,
        ban_type: str,
        reason: str,
        until: datetime | None,
        now: datetime,
    ) -> User:
        user = MemberDirectory(self.session).get(user_id)
        text = reason.strip()
        if not text:
            raise WorkbenchError(400, "ban reason is required")
        if len(text) > 500:
            raise WorkbenchError(400, "ban reason is too long")
        if ban_type == "temporary":
            if until is None:
                raise WorkbenchError(400, "temporary ban requires an end time")
            if until.tzinfo is None:
                raise WorkbenchError(400, "datetime must include a timezone offset")
            if until <= now:
                raise WorkbenchError(400, "ban end time must be in the future")
            end = until
        elif ban_type == "permanent":
            if until is not None:
                raise WorkbenchError(400, "permanent ban does not take an end time")
            end = None
        else:
            raise WorkbenchError(400, "ban type must be temporary or permanent")
        before = UserView.render(user, reveal_phone=True)
        user.ban_type = ban_type
        user.ban_reason = text
        user.ban_until = end
        user.banned_at = now
        self.session.flush()
        self.audit.write(
            "user",
            user.id,
            "ban_user",
            before,
            UserView.render(user, reveal_phone=True),
        )
        return user

    def unban(self, user_id: str) -> User:
        user = MemberDirectory(self.session).get(user_id)
        if user.ban_type is None:
            raise WorkbenchError(409, "user is not banned")
        before = UserView.render(user, reveal_phone=True)
        user.ban_type = None
        user.ban_reason = None
        user.ban_until = None
        user.banned_at = None
        self.session.flush()
        self.audit.write(
            "user",
            user.id,
            "unban_user",
            before,
            UserView.render(user, reveal_phone=True),
        )
        return user

    def grant(self, user_id: str, tier: str, days: int, reason: str, now: datetime) -> Entitlement:
        user = MemberDirectory(self.session).get(user_id)
        text = reason.strip()
        if tier not in MemberAdmin.TIERS:
            raise WorkbenchError(400, "tier must be regular or vip")
        if days < 1 or days > 3660:
            raise WorkbenchError(400, "days must be between 1 and 3660")
        if not text:
            raise WorkbenchError(400, "grant reason is required")
        existing = self.session.scalar(
            select(Entitlement)
            .where(
                Entitlement.user_id == user.id,
                Entitlement.kind == tier,
                Entitlement.expires_at > now,
                or_(
                    Entitlement.source.is_(None),
                    Entitlement.source != EntitlementBook.INVITE,
                ),
            )
            .order_by(Entitlement.expires_at.desc())
        )
        before = None if existing is None else MemberAdmin._entitlement(existing)
        if existing is None:
            grant = Entitlement(
                user_id=user.id,
                kind=tier,
                program_name=None,
                record_id=None,
                expires_at=now + timedelta(days=days),
                source=EntitlementBook.MANUAL,
            )
            self.session.add(grant)
        else:
            grant = existing
            grant.expires_at = grant.expires_at + timedelta(days=days)
            grant.source = EntitlementBook.MANUAL
        self.session.flush()
        self.audit.write(
            "entitlement",
            str(grant.id),
            "grant_membership",
            before,
            {
                **MemberAdmin._entitlement(grant),
                "reason": text,
                "days": days,
            },
        )
        return grant

    @staticmethod
    def _entitlement(row: Entitlement) -> dict:
        return {
            "id": row.id,
            "user_id": row.user_id,
            "kind": row.kind,
            "source": row.source,
            "expires_at": row.expires_at,
        }


class UserProfile:
    @staticmethod
    def detail(session: Session, user: User, *, reveal_phone: bool, now: datetime) -> dict:
        body = UserView.render(user, reveal_phone=reveal_phone)
        grants = list(
            session.scalars(
                select(Entitlement)
                .where(Entitlement.user_id == user.id)
                .order_by(Entitlement.expires_at.desc(), Entitlement.id)
            ).all()
        )
        body["entitlements"] = [UserProfile._grant(row, now) for row in grants]
        codes = list(
            session.scalars(
                select(InviteCode)
                .where(InviteCode.redeemed_by == user.id)
                .order_by(InviteCode.redeemed_at)
            ).all()
        )
        body["invites"] = [
            {
                "code": code.code,
                "channel": code.channel,
                "redeemed_at": code.redeemed_at.isoformat() if code.redeemed_at else None,
            }
            for code in codes
        ]
        return body

    @staticmethod
    def _grant(row: Entitlement, now: datetime) -> dict:
        if row.source == EntitlementBook.MANUAL:
            source_label = "手动发放"
        elif row.source == EntitlementBook.ORDER:
            source_label = "订单"
        elif row.source == EntitlementBook.INVITE:
            source_label = "邀请码"
        else:
            source_label = None
        return {
            "id": row.id,
            "kind": row.kind,
            "program_name": row.program_name,
            "record_id": row.record_id,
            "source": row.source,
            "source_label": source_label,
            "expires_at": row.expires_at.isoformat(),
            "active": row.expires_at > now,
        }
