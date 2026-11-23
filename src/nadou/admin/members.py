from datetime import datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from nadou.admin.audit import AuditWriter
from nadou.admin.errors import WorkbenchError
from nadou.admin.phone import PhoneMask
from nadou.models import Entitlement, InviteCode, Order, OrderIssuance, User
from nadou.payments.catalog import TEST_PRODUCTS


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
        counted = or_(
            Entitlement.source.is_(None),
            Entitlement.source == EntitlementBook.ORDER,
        )
        query = select(Entitlement).where(counted)
        if TEST_PRODUCTS:
            excluded = (
                select(OrderIssuance.entitlement_id)
                .join(Order, Order.id == OrderIssuance.order_id)
                .where(
                    Order.product.in_(TEST_PRODUCTS),
                    OrderIssuance.entitlement_id.is_not(None),
                )
            )
            query = query.where(Entitlement.id.not_in(excluded))
        return list(session.scalars(query).all())

    MEMBERSHIP_KINDS = frozenset({"regular", "vip"})

    @staticmethod
    def current_for_users(
        session: Session, user_ids: list[str], now: datetime
    ) -> dict[str, Entitlement]:
        if not user_ids:
            return {}
        rows = session.scalars(
            select(Entitlement).where(
                Entitlement.user_id.in_(user_ids),
                Entitlement.kind.in_(EntitlementBook.MEMBERSHIP_KINDS),
                Entitlement.expires_at > now,
            )
        ).all()
        current: dict[str, Entitlement] = {}
        for row in rows:
            held = current.get(row.user_id)
            if held is None or EntitlementBook._preferred(row, held):
                current[row.user_id] = row
        return current

    @staticmethod
    def _preferred(candidate: Entitlement, held: Entitlement) -> bool:
        if candidate.kind != held.kind:
            return candidate.kind == "vip"
        return (candidate.expires_at, candidate.id) > (held.expires_at, held.id)


class UserView:
    @staticmethod
    def render(user: User, *, reveal_phone: bool) -> dict:
        phone = user.phone if reveal_phone else PhoneMask.apply(user.phone)
        openid = user.wechat_openid if reveal_phone else None
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

    @staticmethod
    def list_item(user: User, *, reveal_phone: bool, membership: Entitlement | None) -> dict:
        body = UserView.render(user, reveal_phone=reveal_phone)
        body["membership"] = None if membership is None else membership.kind
        body["membership_expires_at"] = (
            None if membership is None else membership.expires_at.isoformat()
        )
        return body


class MemberDirectory:
    LIKE_ESCAPE = "/"
    PAGE_SIZE = 20
    MAX_PAGE_SIZE = 100

    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, user_id: str) -> User:
        user = self.session.get(User, user_id)
        if user is None:
            raise WorkbenchError(404, "user not found")
        return user

    def search(
        self,
        query: str | None,
        *,
        page: int = 1,
        page_size: int = PAGE_SIZE,
    ) -> tuple[list[User], int]:
        page = max(page, 1)
        page_size = min(max(page_size, 1), MemberDirectory.MAX_PAGE_SIZE)
        statement = select(User)
        counted = select(func.count()).select_from(User)
        text = (query or "").strip()
        if text:
            matched = MemberDirectory._matches(text)
            statement = statement.where(matched)
            counted = counted.where(matched)
        total = self.session.scalar(counted) or 0
        rows = list(
            self.session.scalars(
                statement.order_by(User.created_at.desc(), User.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).all()
        )
        return rows, total

    @staticmethod
    def _escape_like(text: str) -> str:
        return (
            text.replace(MemberDirectory.LIKE_ESCAPE, MemberDirectory.LIKE_ESCAPE * 2)
            .replace("%", f"{MemberDirectory.LIKE_ESCAPE}%")
            .replace("_", f"{MemberDirectory.LIKE_ESCAPE}_")
        )

    @staticmethod
    def _matches(text: str):
        escaped = MemberDirectory._escape_like(text)
        contains = f"%{escaped}%"
        prefix = f"{escaped}%"
        return or_(
            User.phone.like(contains, escape=MemberDirectory.LIKE_ESCAPE),
            User.display_name.ilike(contains, escape=MemberDirectory.LIKE_ESCAPE),
            User.wechat_openid.like(prefix, escape=MemberDirectory.LIKE_ESCAPE),
            User.id == text,
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
