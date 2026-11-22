"""Create or reset a workbench admin. The password never comes from argv."""

import getpass
import logging
import secrets
import sys
from datetime import datetime

from sqlalchemy import select

from nadou.admin.audit import AuditWriter
from nadou.admin.auth import StaffAuthenticator, StaffDirectory, StaffSessions
from nadou.admin.errors import WorkbenchError
from nadou.models import StaffAccount

logger = logging.getLogger(__name__)


class _Options:
    def __init__(self, username: str, reset: bool, generate: bool, help_: bool) -> None:
        self.username = username
        self.reset = reset
        self.generate = generate
        self.help = help_


class _UsageError(Exception):
    pass


class _InputError(Exception):
    pass


class CreateAdminCommand:
    """Server-side bootstrap for the first workbench admin.

    The password is read twice from the terminal, once from stdin when stdin
    is not a terminal, or generated and printed a single time. It is not
    accepted as an argument, and it is not written to the audit log.
    """

    MIN_PASSWORD = 12
    MAX_PASSWORD = 128

    @staticmethod
    def run(argv: list[str] | None = None) -> int:
        args = list(sys.argv[1:] if argv is None else argv)
        if args[:1] == ["create-admin"]:
            args = args[1:]
        try:
            options = CreateAdminCommand._parse(args)
        except _UsageError as exc:
            print(str(exc), file=sys.stderr)
            print(CreateAdminCommand.usage(), file=sys.stderr)
            return 2
        if options.help:
            print(CreateAdminCommand.usage())
            return 0
        try:
            password, generated = CreateAdminCommand._password(options.generate)
        except _InputError as exc:
            logger.warning("create-admin rejected password input")
            print(str(exc), file=sys.stderr)
            return 2
        try:
            StaffDirectory.check_password(password, CreateAdminCommand.MIN_PASSWORD)
        except WorkbenchError as exc:
            logger.warning("create-admin rejected weak password")
            print(CreateAdminCommand._detail(exc), file=sys.stderr)
            return 2
        return CreateAdminCommand._apply(options, password, generated)

    @staticmethod
    def usage() -> str:
        return (
            "usage: python -m nadou create-admin USERNAME [--reset-password] [--generate]\n"
            "The password is read twice from the terminal, once from stdin when stdin\n"
            "is not a terminal, or generated with --generate and printed once."
        )

    @staticmethod
    def _parse(argv: list[str]) -> _Options:
        username = ""
        reset = False
        generate = False
        help_ = False
        for arg in argv:
            if arg in {"--help", "-h"}:
                help_ = True
            elif arg == "--reset-password":
                reset = True
            elif arg == "--generate":
                generate = True
            elif arg.startswith("-"):
                raise _UsageError("unrecognized argument")
            elif not username:
                username = arg
            else:
                raise _UsageError("unrecognized argument")
        if help_:
            return _Options(username, reset, generate, True)
        if not username:
            raise _UsageError("username is required")
        return _Options(username, reset, generate, False)

    @staticmethod
    def _password(generate: bool) -> tuple[str, bool]:
        if generate:
            return secrets.token_urlsafe(18), True
        if not CreateAdminCommand._interactive():
            return CreateAdminCommand._from_stdin(), False
        first = getpass.getpass("Password: ")
        second = getpass.getpass("Confirm password: ")
        if first != second:
            raise _InputError("passwords do not match")
        return first, False

    @staticmethod
    def _interactive() -> bool:
        stdin = sys.stdin
        isatty = getattr(stdin, "isatty", None)
        return bool(callable(isatty) and isatty())

    @staticmethod
    def _from_stdin() -> str:
        if sys.stdin is None:
            raise _InputError("password is required")
        text = CreateAdminCommand._read_bounded(CreateAdminCommand.MAX_PASSWORD * 2 + 4)
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        if text.endswith("\n"):
            text = text[:-1]
        parts = text.split("\n")
        if len(parts) == 1:
            return parts[0]
        if len(parts) == 2 and parts[0] == parts[1]:
            return parts[0]
        if len(parts) == 2:
            raise _InputError("passwords do not match")
        raise _InputError("stdin password must be one line")

    @staticmethod
    def _read_bounded(limit: int) -> str:
        chunks: list[str] = []
        remaining = limit + 1
        while remaining > 0:
            piece = sys.stdin.read(remaining)
            if not piece:
                break
            chunks.append(piece)
            remaining -= len(piece)
        text = "".join(chunks)
        if len(text) > limit:
            raise _InputError(
                "password must be "
                f"{CreateAdminCommand.MIN_PASSWORD} to {CreateAdminCommand.MAX_PASSWORD} "
                "characters"
            )
        return text

    @staticmethod
    def _apply(options: _Options, password: str, generated: bool) -> int:
        from nadou.config import get_settings
        from nadou.db import Database
        from nadou.text import RecordAge

        database = None
        session = None
        try:
            database = Database(get_settings().database_url)
            session = database.session()
            now = datetime.now(RecordAge.TZ)
            if options.reset:
                account, locks = CreateAdminCommand._reset(session, options.username, password, now)
                action = "reset_password"
                before = {
                    "username": account.username,
                    "role": account.role,
                    "locks": locks,
                }
            else:
                account = StaffDirectory.create(
                    session,
                    options.username,
                    password,
                    "admin",
                    now,
                    minimum_password=CreateAdminCommand.MIN_PASSWORD,
                )
                action = "create_admin"
                before = None
                locks = []
            after = {"username": account.username, "role": account.role}
            if options.reset:
                after["locks_cleared"] = len(locks)
            AuditWriter(session, now, None, None).write(
                "staff_account",
                account.id,
                action,
                before,
                after,
            )
            session.commit()
            username = account.username
        except WorkbenchError as exc:
            if session is not None:
                session.rollback()
            logger.warning(
                "create-admin rejected username=%s status=%s",
                options.username,
                exc.status_code,
            )
            print(CreateAdminCommand._detail(exc), file=sys.stderr)
            return 2
        except Exception as exc:
            if session is not None:
                session.rollback()
            kind = type(exc).__name__
            logger.error("create-admin failed error=%s", kind)
            print(f"create-admin failed: {kind}", file=sys.stderr)
            return 1
        finally:
            if session is not None:
                session.close()
            if database is not None:
                database.engine.dispose()
        CreateAdminCommand._report(username, options.reset, password if generated else None)
        return 0

    @staticmethod
    def _reset(
        session, username: str, password: str, now: datetime
    ) -> tuple[StaffAccount, list[dict]]:
        name = StaffDirectory.normalize_username(username)
        existing = session.scalar(select(StaffAccount).where(StaffAccount.username == name))
        if existing is None:
            raise WorkbenchError(404, "username not found")
        if existing.role != "admin":
            raise WorkbenchError(409, "account is not an admin")
        account = StaffDirectory.reset_password(
            session,
            name,
            password,
            minimum_password=CreateAdminCommand.MIN_PASSWORD,
        )
        StaffSessions.revoke_account(session, account.id, now)
        locks = StaffAuthenticator.clear_locks(session, account.id)
        return account, locks

    @staticmethod
    def _detail(exc: WorkbenchError) -> str:
        if isinstance(exc.detail, str):
            return exc.detail
        return "create-admin failed"

    @staticmethod
    def _report(username: str, reset: bool, generated_password: str | None) -> None:
        action = "reset" if reset else "created"
        logger.info("create-admin %s username=%s", action, username)
        if reset:
            print(f"reset password for account: {username}")
        else:
            print(f"created admin account: {username}")
        if generated_password is not None:
            print(f"generated password: {generated_password}")
