import secrets
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from nadou.admin.audit import AuditWriter
from nadou.admin.errors import WorkbenchError
from nadou.admin.phone import PhoneMask
from nadou.models import Entitlement, InviteCode, Order, StaffAccount, User


class InviteAlphabet:
    """Eight characters, uppercase, with the ambiguous glyphs removed."""

    CHARACTERS = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    LENGTH = 8

    @staticmethod
    def new_code() -> str:
        return "".join(
            secrets.choice(InviteAlphabet.CHARACTERS) for _ in range(InviteAlphabet.LENGTH)
        )

    @staticmethod
    def valid(code: str) -> bool:
        return len(code) == InviteAlphabet.LENGTH and all(
            char in InviteAlphabet.CHARACTERS for char in code
        )


class InviteStatus:
    UNUSED = "未使用"
    REDEEMED = "已兑换"
    EXPIRED = "已过期"
    VOIDED = "已作废"

    @staticmethod
    def of(row: InviteCode, now: datetime) -> str:
        if row.voided_at is not None:
            return InviteStatus.VOIDED
        if row.redeemed_at is not None:
            return InviteStatus.REDEEMED
        if row.expires_at <= now:
            return InviteStatus.EXPIRED
        return InviteStatus.UNUSED


class InviteView:
    @staticmethod
    def render(row: InviteCode, now: datetime, phone: str | None, creator: str = "") -> dict:
        return {
            "id": row.id,
            "code": row.code,
            "channel": row.channel,
            "status": InviteStatus.of(row, now),
            "created_by": row.created_by,
            "creator": creator,
            "created_at": row.created_at.isoformat(),
            "expires_at": row.expires_at.isoformat(),
            "redeemed_phone": PhoneMask.apply(phone),
            "redeemed_at": row.redeemed_at.isoformat() if row.redeemed_at else None,
            "voided_at": row.voided_at.isoformat() if row.voided_at else None,
            "voided_by": row.voided_by,
        }


class InviteStats:
    """Paid conversion counts mini-program orders only, inside 30 days of redemption."""

    WINDOW = timedelta(days=30)
    PAID = frozenset({"已支付", "已发放", "发放异常", "已退款"})

    @staticmethod
    def channels(session: Session, now: datetime) -> list[dict]:
        codes = list(session.scalars(select(InviteCode)).all())
        orders = list(session.scalars(select(Order)).all())
        paid_at: dict[str, list[datetime]] = {}
        for order in orders:
            if order.status not in InviteStats.PAID:
                continue
            when = order.paid_at or order.created_at
            paid_at.setdefault(order.user_id, []).append(when)
        grouped: dict[str, list[InviteCode]] = {}
        for row in codes:
            grouped.setdefault(row.channel, []).append(row)
        result = []
        for channel in sorted(grouped):
            rows = grouped[channel]
            counts = {name: 0 for name in ("未使用", "已兑换", "已过期", "已作废")}
            paid = 0
            for row in rows:
                counts[InviteStatus.of(row, now)] += 1
                if row.redeemed_at is None or row.redeemed_by is None:
                    continue
                window_end = row.redeemed_at + InviteStats.WINDOW
                moments = paid_at.get(row.redeemed_by, [])
                if any(row.redeemed_at <= moment <= window_end for moment in moments):
                    paid += 1
            result.append(
                {
                    "channel": channel,
                    "generated": len(rows),
                    "redeemed": counts["已兑换"],
                    "unused": counts["未使用"],
                    "expired": counts["已过期"],
                    "voided": counts["已作废"],
                    "paid_within_30_days": paid,
                }
            )
        return result


class InviteRedemption:
    """Bind and consume a code in one transaction. The 30 days run beside VIP, not after it."""

    DAYS = 30

    def __init__(self, session: Session, audit: AuditWriter, now: datetime) -> None:
        self.session = session
        self.audit = audit
        self.now = now

    def redeem(self, code: str, user_id: str) -> Entitlement:
        from nadou.admin.members import EntitlementBook

        # Serialize both competing codes for one user and competing users for one code.
        # Locks live until the request transaction commits or rolls back.
        user = self.session.scalar(select(User).where(User.id == user_id).with_for_update())
        if user is None:
            raise WorkbenchError(404, "user not found")
        token = code.strip().upper()
        row = self.session.scalar(
            select(InviteCode)
            .where(InviteCode.code == token)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None or row.voided_at is not None:
            raise WorkbenchError(400, "invalid")
        if row.redeemed_at is not None:
            raise WorkbenchError(409, "used")
        if row.expires_at <= self.now:
            raise WorkbenchError(409, "expired")
        already = self.session.scalar(
            select(InviteCode.id).where(InviteCode.redeemed_by == user.id)
        )
        if already is not None:
            raise WorkbenchError(409, "bound")
        grant = Entitlement(
            user_id=user.id,
            kind="regular",
            program_name=None,
            record_id=None,
            expires_at=self.now + timedelta(days=InviteRedemption.DAYS),
            source=EntitlementBook.INVITE,
        )
        row.redeemed_by = user.id
        row.redeemed_at = self.now
        self.session.add(grant)
        self.session.flush()
        self.audit.write(
            "invite_code",
            row.id,
            "redeem_invite",
            None,
            {
                "code": row.code,
                "user_id": user.id,
                "expires_at": grant.expires_at.isoformat(),
            },
        )
        return grant


class InviteBook:
    MAX_BATCH = 200

    def __init__(self, session: Session, audit: AuditWriter, now: datetime) -> None:
        self.session = session
        self.audit = audit
        self.now = now

    def generate(
        self, count: int, channel: str, expires_at: datetime, actor: StaffAccount
    ) -> list[InviteCode]:
        label = channel.strip()
        if count < 1 or count > InviteBook.MAX_BATCH:
            raise WorkbenchError(400, "count must be between 1 and 200")
        if not label or len(label) > 64:
            raise WorkbenchError(400, "channel is required")
        if expires_at.tzinfo is None:
            raise WorkbenchError(400, "datetime must include a timezone offset")
        if expires_at <= self.now:
            raise WorkbenchError(400, "expiry must be in the future")
        created: list[InviteCode] = []
        seen = set(self.session.scalars(select(InviteCode.code)).all())
        for _ in range(count):
            code = self._unique(seen)
            row = InviteCode(
                id=str(uuid4()),
                code=code,
                channel=label,
                created_by=actor.id,
                created_at=self.now,
                expires_at=expires_at,
                redeemed_by=None,
                redeemed_at=None,
                voided_at=None,
                voided_by=None,
            )
            self.session.add(row)
            created.append(row)
        self.session.flush()
        self.audit.write(
            "invite_code",
            created[0].id,
            "create_invites",
            None,
            {"channel": label, "count": count, "codes": [row.code for row in created]},
        )
        return created

    def void(self, code_id: str, actor: StaffAccount) -> InviteCode:
        row = self.session.scalar(
            select(InviteCode)
            .where(InviteCode.id == code_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise WorkbenchError(404, "invite code not found")
        if InviteStatus.of(row, self.now) != InviteStatus.UNUSED:
            raise WorkbenchError(409, "only an unused code can be voided")
        before = {"code": row.code, "status": InviteStatus.UNUSED, "channel": row.channel}
        row.voided_at = self.now
        row.voided_by = actor.username
        self.session.flush()
        self.audit.write(
            "invite_code",
            row.id,
            "void_invite",
            before,
            {"code": row.code, "status": InviteStatus.VOIDED, "channel": row.channel},
        )
        return row

    def list_codes(
        self, *, status: str | None, channel: str | None, query: str | None
    ) -> list[dict]:
        rows = list(
            self.session.scalars(
                select(InviteCode).order_by(InviteCode.created_at.desc(), InviteCode.id)
            ).all()
        )
        needle = (query or "").strip().casefold()
        channel_filter = (channel or "").strip()
        shown = []
        phones = self._phones(rows)
        creators = self._creators(rows)
        for row in rows:
            current = InviteStatus.of(row, self.now)
            if status and current != status:
                continue
            if channel_filter and row.channel != channel_filter:
                continue
            haystack = f"{row.code} {row.channel}".casefold()
            if needle and needle not in haystack:
                continue
            shown.append(
                InviteView.render(
                    row,
                    self.now,
                    phones.get(row.redeemed_by or ""),
                    creators.get(row.created_by, ""),
                )
            )
        return shown

    def _creators(self, rows: list[InviteCode]) -> dict[str, str]:
        ids = {row.created_by for row in rows if row.created_by}
        if not ids:
            return {}
        staff = self.session.scalars(select(StaffAccount).where(StaffAccount.id.in_(ids))).all()
        return {item.id: item.username for item in staff}

    def _phones(self, rows: list[InviteCode]) -> dict[str, str | None]:
        ids = {row.redeemed_by for row in rows if row.redeemed_by}
        if not ids:
            return {}
        users = self.session.scalars(select(User).where(User.id.in_(ids))).all()
        return {user.id: user.phone for user in users}

    def _unique(self, seen: set[str]) -> str:
        for _ in range(50):
            code = InviteAlphabet.new_code()
            if code not in seen:
                seen.add(code)
                return code
        raise WorkbenchError(409, "could not allocate an invite code")
