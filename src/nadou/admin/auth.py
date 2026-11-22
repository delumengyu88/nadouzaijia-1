import hashlib
import ipaddress
import secrets
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from nadou.admin.audit import AuditWriter
from nadou.admin.errors import WorkbenchError
from nadou.admin.passwords import PasswordHasher
from nadou.config import get_settings, parse_trusted_proxies
from nadou.models import StaffAccount, StaffLoginLock, StaffSession


class StaffSessions:
    """Opaque server-side session. The raw token is never stored."""

    COOKIE_NAME = "nadou_admin_session"
    TTL = timedelta(hours=12)

    @staticmethod
    def hash_token(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    @staticmethod
    def issue(session: Session, account: StaffAccount, now: datetime) -> str:
        token = secrets.token_urlsafe(32)
        session.add(
            StaffSession(
                id=str(uuid4()),
                token_hash=StaffSessions.hash_token(token),
                account_id=account.id,
                created_at=now,
                expires_at=now + StaffSessions.TTL,
                revoked_at=None,
            )
        )
        session.flush()
        return token

    @staticmethod
    def resolve(session: Session, token: str | None, now: datetime) -> StaffAccount | None:
        row = StaffSessions._row(session, token)
        if row is None or row.revoked_at is not None or row.expires_at <= now:
            return None
        return session.get(StaffAccount, row.account_id)

    @staticmethod
    def revoke(session: Session, token: str | None, now: datetime) -> None:
        row = StaffSessions._row(session, token)
        if row is None or row.revoked_at is not None:
            return
        row.revoked_at = now
        session.flush()

    @staticmethod
    def revoke_account(session: Session, account_id: str, now: datetime) -> None:
        """Revoke every unexpired session for one account. Logout uses the same revoked_at flag."""
        rows = session.scalars(
            select(StaffSession).where(
                StaffSession.account_id == account_id,
                StaffSession.revoked_at.is_(None),
                StaffSession.expires_at > now,
            )
        ).all()
        if not rows:
            return
        for row in rows:
            row.revoked_at = now
        session.flush()

    @staticmethod
    def _row(session: Session, token: str | None) -> StaffSession | None:
        if not token:
            return None
        digest = StaffSessions.hash_token(token)
        return session.scalar(select(StaffSession).where(StaffSession.token_hash == digest))


class StaffDirectory:
    ROLES = frozenset({"support", "admin"})
    MIN_PASSWORD = 8
    MAX_PASSWORD = 128

    @staticmethod
    def normalize_username(username: str) -> str:
        name = username.strip()
        if not name or len(name) > 64 or any(char.isspace() for char in name):
            raise WorkbenchError(400, "username is required")
        return name

    @staticmethod
    def check_password(password: str, minimum: int) -> None:
        if len(password) < minimum or len(password) > StaffDirectory.MAX_PASSWORD:
            raise WorkbenchError(
                400,
                f"password must be {minimum} to {StaffDirectory.MAX_PASSWORD} characters",
            )

    @staticmethod
    def create(
        session: Session,
        username: str,
        password: str,
        role: str,
        now: datetime,
        *,
        minimum_password: int = MIN_PASSWORD,
    ) -> StaffAccount:
        name = StaffDirectory.normalize_username(username)
        StaffDirectory.check_password(password, minimum_password)
        if role not in StaffDirectory.ROLES:
            raise WorkbenchError(400, "role must be support or admin")
        taken = session.scalar(select(StaffAccount).where(StaffAccount.username == name))
        if taken is not None:
            raise WorkbenchError(409, "username already exists")
        account = StaffAccount(
            id=str(uuid4()),
            username=name,
            password_hash=PasswordHasher.hash(password),
            role=role,
            created_at=now,
        )
        session.add(account)
        session.flush()
        return account

    @staticmethod
    def reset_password(
        session: Session,
        username: str,
        password: str,
        *,
        minimum_password: int,
    ) -> StaffAccount:
        name = StaffDirectory.normalize_username(username)
        StaffDirectory.check_password(password, minimum_password)
        account = session.scalar(select(StaffAccount).where(StaffAccount.username == name))
        if account is None:
            raise WorkbenchError(404, "username not found")
        account.password_hash = PasswordHasher.hash(password)
        session.flush()
        return account

    @staticmethod
    def active_locks(session: Session, now: datetime) -> list[dict]:
        rows = list(
            session.scalars(
                select(StaffLoginLock).where(StaffLoginLock.locked_until.is_not(None))
            ).all()
        )
        listed = []
        for row in rows:
            if row.locked_until is None or row.locked_until <= now:
                continue
            account = session.get(StaffAccount, row.account_id)
            if account is None:
                continue
            listed.append(
                {
                    "account_id": account.id,
                    "username": account.username,
                    "role": account.role,
                    "ip": row.ip,
                    "locked_until": row.locked_until.isoformat(),
                    "consecutive_failures": row.consecutive_failures,
                }
            )
        listed.sort(key=lambda item: item["username"])
        return listed


class StaffAuthenticator:
    """Five failures lock that account from that IP for 30 minutes. Other IPs are unaffected."""

    MAX_FAILURES = 5
    LOCK = timedelta(minutes=30)

    def __init__(self, session: Session, now: datetime, audit: AuditWriter) -> None:
        self.session = session
        self.now = now
        self.audit = audit

    def login(self, username: str, password: str, ip: str) -> tuple[str, StaffAccount]:
        account = self.session.scalar(
            select(StaffAccount).where(StaffAccount.username == username.strip())
        )
        if account is not None:
            self._expire_lock(account.id, ip)
            self._reject_if_locked(account.id, ip)
        if account is None or not PasswordHasher.verify(account.password_hash, password):
            if account is not None:
                self._record_failure(account, ip)
                self._reject_if_locked(account.id, ip)
            raise WorkbenchError(401, "invalid credentials")
        self._clear(account.id, ip)
        token = StaffSessions.issue(self.session, account, self.now)
        self.audit.actor = account
        self.audit.write(
            "staff_account",
            account.id,
            "login",
            None,
            {"username": account.username, "role": account.role},
        )
        return token, account

    def unlock(self, account_id: str, actor: StaffAccount) -> StaffAccount:
        account = self.session.get(StaffAccount, account_id)
        if account is None:
            raise WorkbenchError(404, "staff account not found")
        before = StaffAuthenticator.clear_locks(self.session, account_id)
        self.audit.actor = actor
        self.audit.write("staff_account", account.id, "unlock", {"locks": before}, {"locks": []})
        return account

    @staticmethod
    def clear_locks(session: Session, account_id: str) -> list[dict]:
        rows = list(
            session.scalars(
                select(StaffLoginLock).where(StaffLoginLock.account_id == account_id)
            ).all()
        )
        before = [
            {
                "ip": row.ip,
                "consecutive_failures": row.consecutive_failures,
                "locked_until": row.locked_until,
            }
            for row in rows
        ]
        for row in rows:
            session.delete(row)
        session.flush()
        return before

    def _expire_lock(self, account_id: str, ip: str) -> None:
        row = self._lock(account_id, ip)
        if row is None or row.locked_until is None:
            return
        if row.locked_until <= self.now:
            row.consecutive_failures = 0
            row.locked_until = None
            self.session.flush()

    def _reject_if_locked(self, account_id: str, ip: str) -> None:
        row = self._lock(account_id, ip)
        if row is None or row.locked_until is None or row.locked_until <= self.now:
            return
        raise WorkbenchError(
            423,
            {"message": "locked", "locked_until": row.locked_until.isoformat()},
        )

    def _record_failure(self, account: StaffAccount, ip: str) -> None:
        row = self._lock(account.id, ip)
        if row is not None and row.locked_until is not None and row.locked_until > self.now:
            return
        if row is None:
            row = StaffLoginLock(
                account_id=account.id,
                ip=ip,
                consecutive_failures=0,
                locked_until=None,
            )
            self.session.add(row)
        row.consecutive_failures += 1
        if row.consecutive_failures >= StaffAuthenticator.MAX_FAILURES:
            row.locked_until = self.now + StaffAuthenticator.LOCK
            self.audit.actor = account
            self.audit.write(
                "staff_lock",
                account.id,
                "lock",
                None,
                {
                    "ip": ip,
                    "consecutive_failures": row.consecutive_failures,
                    "locked_until": row.locked_until,
                },
            )
        self.session.flush()

    def _clear(self, account_id: str, ip: str) -> None:
        row = self._lock(account_id, ip)
        if row is None:
            return
        self.session.delete(row)
        self.session.flush()

    def _lock(self, account_id: str, ip: str) -> StaffLoginLock | None:
        return self.session.scalar(
            select(StaffLoginLock).where(
                StaffLoginLock.account_id == account_id,
                StaffLoginLock.ip == ip,
            )
        )


class RequestToken:
    @staticmethod
    def read(request) -> str | None:
        header = request.headers.get("authorization", "")
        if header.lower().startswith("bearer "):
            token = header[7:].strip()
            if token:
                return token
        cookie = request.cookies.get(StaffSessions.COOKIE_NAME)
        return cookie or None


class ClientAddress:
    """Direct peer, or X-Forwarded-For when that peer is a configured trusted proxy."""

    @staticmethod
    def of(request) -> str:
        client = request.client
        if client is None or not client.host:
            return "unknown"
        peer = client.host
        if not ClientAddress._trusted(peer):
            return peer
        forwarded = ClientAddress._forwarded(request.headers.get("x-forwarded-for"))
        if forwarded is None:
            return peer
        return forwarded

    @staticmethod
    def _trusted(peer: str) -> bool:
        try:
            address = ipaddress.ip_address(peer)
        except ValueError:
            return False
        networks = parse_trusted_proxies(get_settings().trusted_proxies)
        return any(address in network for network in networks)

    @staticmethod
    def _forwarded(header: str | None) -> str | None:
        if not header:
            return None
        rightmost = header.split(",")[-1].strip()
        if not rightmost:
            return None
        try:
            ipaddress.ip_address(rightmost)
        except ValueError:
            return None
        return rightmost
