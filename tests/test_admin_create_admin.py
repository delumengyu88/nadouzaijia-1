import io
import logging
import os
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from nadou.__main__ import NadouCommand
from nadou.admin.auth import StaffDirectory
from nadou.admin.demo_data import DemoAccounts
from nadou.config import get_settings
from nadou.db import Database
from nadou.main import create_app
from nadou.models import AuditLog, StaffAccount
from tests.workbench_helpers import auth

USERNAME = "ops-admin"
PASSWORD = "passphrase12"
REPLACEMENT = "replacement-secret"
WEAK = "passphrase1"


@pytest.fixture
def command_db(monkeypatch, session):
    monkeypatch.setenv("DATABASE_URL", str(session.get_bind().url))
    monkeypatch.delenv("NADOU_ALLOW_DEV_SEED", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _run(argv: list[str]) -> int:
    try:
        NadouCommand.main(["create-admin", *argv])
    except SystemExit as exc:
        code = 0 if exc.code is None else exc.code
        assert isinstance(code, int)
        return code
    raise AssertionError("create-admin did not exit")


def _captured(capsys) -> str:
    outcome = capsys.readouterr()
    return outcome.out + outcome.err


def _audit_blob(session) -> str:
    session.expire_all()
    rows = list(session.scalars(select(AuditLog)).all())
    parts: list[str] = []
    for row in rows:
        parts.extend(
            [
                row.action,
                row.object_type,
                row.object_id,
                str(row.actor_username),
                str(row.before_json),
                str(row.after_json),
                str(row.ip),
            ]
        )
    return "\n".join(parts)


def _watch_logs(caplog):
    caplog.set_level(logging.DEBUG)


def test_stdin_account_can_sign_in_and_sign_out(
    command_db, session, clock, capsys, caplog, monkeypatch
):
    _watch_logs(caplog)
    monkeypatch.setattr(sys, "stdin", io.StringIO(PASSWORD + "\n"))

    def fail_getpass(_prompt: str = "") -> str:
        raise AssertionError("getpass")

    monkeypatch.setattr("nadou.admin.create_admin.getpass.getpass", fail_getpass)
    assert _run([USERNAME]) == 0
    output = _captured(capsys)
    assert PASSWORD not in output
    assert PASSWORD not in caplog.text
    assert f"created admin account: {USERNAME}" in output

    session.expire_all()
    stored = session.scalar(select(StaffAccount).where(StaffAccount.username == USERNAME))
    assert stored is not None
    assert stored.role == "admin"
    assert stored.password_hash != PASSWORD
    assert PASSWORD not in stored.password_hash
    assert DemoAccounts.ADMIN not in set(session.scalars(select(StaffAccount.username)).all())
    blob = _audit_blob(session)
    assert "create_admin" in blob
    assert PASSWORD not in blob
    assert "password" not in blob.casefold()

    app = create_app(database=Database(engine=session.get_bind()), now=lambda: clock)
    with TestClient(app) as client:
        logged = client.post(
            "/api/v1/admin/auth/login",
            json={"username": USERNAME, "password": PASSWORD},
        )
        assert logged.status_code == 200
        token = logged.json()["token"]
        assert logged.json()["role"] == "admin"
        me = client.get("/api/v1/admin/auth/me", headers=auth(token))
        assert me.status_code == 200
        assert me.json()["username"] == USERNAME
        staff = client.get("/api/v1/admin/staff", headers=auth(token))
        assert staff.status_code == 200
        assert staff.json()[0]["username"] == USERNAME
        gone = client.post("/api/v1/admin/auth/logout", headers=auth(token))
        assert gone.status_code == 200
        denied = client.get("/api/v1/admin/auth/me", headers=auth(token))
        assert denied.status_code == 401
        denied_staff = client.get("/api/v1/admin/staff", headers=auth(token))
        assert denied_staff.status_code == 401


def test_duplicate_username_does_not_replace_the_password(
    command_db, session, clock, capsys, caplog, monkeypatch
):
    _watch_logs(caplog)
    monkeypatch.setattr(sys, "stdin", io.StringIO(PASSWORD + "\n"))
    assert _run([USERNAME]) == 0
    capsys.readouterr()
    monkeypatch.setattr(sys, "stdin", io.StringIO(REPLACEMENT + "\n"))
    assert _run([USERNAME]) == 2
    output = _captured(capsys)
    assert "username already exists" in output
    assert PASSWORD not in output
    assert REPLACEMENT not in output
    assert PASSWORD not in caplog.text
    assert REPLACEMENT not in caplog.text
    session.expire_all()
    assert _audit_blob(session).count("create_admin") == 1
    assert "reset_password" not in _audit_blob(session)

    app = create_app(database=Database(engine=session.get_bind()), now=lambda: clock)
    with TestClient(app) as client:
        original = client.post(
            "/api/v1/admin/auth/login",
            json={"username": USERNAME, "password": PASSWORD},
        )
        rejected = client.post(
            "/api/v1/admin/auth/login",
            json={"username": USERNAME, "password": REPLACEMENT},
        )
    assert original.status_code == 200
    assert rejected.status_code == 401


def test_reset_password_replaces_only_an_admin(
    command_db, session, clock, capsys, caplog, monkeypatch
):
    _watch_logs(caplog)
    monkeypatch.setattr(sys, "stdin", io.StringIO(PASSWORD + "\n"))
    assert _run([USERNAME]) == 0
    capsys.readouterr()
    monkeypatch.setattr(sys, "stdin", io.StringIO(REPLACEMENT + "\n"))
    assert _run([USERNAME, "--reset-password"]) == 0
    output = _captured(capsys)
    assert f"reset password for account: {USERNAME}" in output
    assert PASSWORD not in output
    assert REPLACEMENT not in output
    assert PASSWORD not in caplog.text
    assert REPLACEMENT not in caplog.text
    blob = _audit_blob(session)
    assert "reset_password" in blob
    assert PASSWORD not in blob
    assert REPLACEMENT not in blob

    app = create_app(database=Database(engine=session.get_bind()), now=lambda: clock)
    with TestClient(app) as client:
        old = client.post(
            "/api/v1/admin/auth/login",
            json={"username": USERNAME, "password": PASSWORD},
        )
        new = client.post(
            "/api/v1/admin/auth/login",
            json={"username": USERNAME, "password": REPLACEMENT},
        )
    assert old.status_code == 401
    assert new.status_code == 200
    assert new.json()["role"] == "admin"


def test_reset_password_leaves_a_support_account_unchanged(
    command_db, session, clock, capsys, caplog, monkeypatch
):
    _watch_logs(caplog)
    StaffDirectory.create(session, "desk", PASSWORD, "support", clock)
    session.commit()
    monkeypatch.setattr(sys, "stdin", io.StringIO(REPLACEMENT + "\n"))
    assert _run(["desk", "--reset-password"]) == 2
    output = _captured(capsys)
    assert "account is not an admin" in output
    assert PASSWORD not in output
    assert REPLACEMENT not in output
    assert PASSWORD not in caplog.text
    assert REPLACEMENT not in caplog.text
    session.expire_all()
    assert "reset_password" not in _audit_blob(session)

    app = create_app(database=Database(engine=session.get_bind()), now=lambda: clock)
    with TestClient(app) as client:
        kept = client.post(
            "/api/v1/admin/auth/login",
            json={"username": "desk", "password": PASSWORD},
        )
        rejected = client.post(
            "/api/v1/admin/auth/login",
            json={"username": "desk", "password": REPLACEMENT},
        )
    assert kept.status_code == 200
    assert kept.json()["role"] == "support"
    assert rejected.status_code == 401


def test_reset_of_unknown_username_does_not_create(
    command_db, session, capsys, caplog, monkeypatch
):
    _watch_logs(caplog)
    monkeypatch.setattr(sys, "stdin", io.StringIO(PASSWORD + "\n"))
    assert _run([USERNAME, "--reset-password"]) == 2
    output = _captured(capsys)
    assert "username not found" in output
    assert PASSWORD not in output
    assert PASSWORD not in caplog.text
    session.expire_all()
    assert session.scalars(select(StaffAccount.id)).all() == []
    assert session.scalars(select(AuditLog.id)).all() == []


def test_weak_password_is_rejected(command_db, session, capsys, caplog, monkeypatch):
    _watch_logs(caplog)
    monkeypatch.setattr(sys, "stdin", io.StringIO(WEAK + "\n"))
    assert _run([USERNAME]) == 2
    output = _captured(capsys)
    assert "password must be 12 to 128 characters" in output
    assert WEAK not in output
    assert WEAK not in caplog.text
    session.expire_all()
    assert session.scalars(select(StaffAccount.id)).all() == []
    assert session.scalars(select(AuditLog.id)).all() == []

    monkeypatch.setattr(sys, "stdin", io.StringIO("x" * 129 + "\n"))
    assert _run([USERNAME]) == 2
    assert session.scalars(select(StaffAccount.id)).all() == []
    assert "x" * 129 not in _captured(capsys)
    assert "x" * 129 not in caplog.text


def test_generate_prints_the_password_once(command_db, session, clock, capsys, caplog):
    _watch_logs(caplog)
    assert _run([USERNAME, "--generate"]) == 0
    outcome = capsys.readouterr()
    prefix = "generated password: "
    line = next(item for item in outcome.out.splitlines() if item.startswith(prefix))
    password = line.removeprefix(prefix)
    assert len(password) >= 12
    assert outcome.out.count(password) == 1
    assert password not in outcome.err
    assert password not in caplog.text
    blob = _audit_blob(session)
    assert password not in blob
    assert "create_admin" in blob

    app = create_app(database=Database(engine=session.get_bind()), now=lambda: clock)
    with TestClient(app) as client:
        logged = client.post(
            "/api/v1/admin/auth/login",
            json={"username": USERNAME, "password": password},
        )
    assert logged.status_code == 200


def test_interactive_entry_reads_the_password_twice(
    command_db, session, clock, capsys, caplog, monkeypatch
):
    _watch_logs(caplog)
    prompts: list[str] = []
    answers = iter([PASSWORD, PASSWORD])

    def fake_getpass(prompt: str = "") -> str:
        prompts.append(prompt)
        return next(answers)

    class _Terminal(io.StringIO):
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr(sys, "stdin", _Terminal())
    monkeypatch.setattr("nadou.admin.create_admin.getpass.getpass", fake_getpass)
    assert _run([USERNAME]) == 0
    assert prompts == ["Password: ", "Confirm password: "]
    output = _captured(capsys)
    assert PASSWORD not in output
    assert PASSWORD not in caplog.text

    app = create_app(database=Database(engine=session.get_bind()), now=lambda: clock)
    with TestClient(app) as client:
        logged = client.post(
            "/api/v1/admin/auth/login",
            json={"username": USERNAME, "password": PASSWORD},
        )
    assert logged.status_code == 200


def test_mismatched_confirmation_creates_nothing(command_db, session, capsys, caplog, monkeypatch):
    _watch_logs(caplog)
    answers = iter([PASSWORD, REPLACEMENT])

    def fake_getpass(prompt: str = "") -> str:
        return next(answers)

    class _Terminal(io.StringIO):
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr(sys, "stdin", _Terminal())
    monkeypatch.setattr("nadou.admin.create_admin.getpass.getpass", fake_getpass)
    assert _run([USERNAME]) == 2
    output = _captured(capsys)
    assert "passwords do not match" in output
    assert PASSWORD not in output
    assert REPLACEMENT not in output
    assert PASSWORD not in caplog.text
    assert REPLACEMENT not in caplog.text
    session.expire_all()
    assert session.scalars(select(StaffAccount.id)).all() == []

    monkeypatch.setattr(sys, "stdin", io.StringIO(PASSWORD + "\n" + REPLACEMENT + "\n"))
    assert _run([USERNAME]) == 2
    output = _captured(capsys)
    assert "passwords do not match" in output
    assert PASSWORD not in output
    assert REPLACEMENT not in output


def test_password_on_the_command_line_is_refused(command_db, session, capsys, caplog):
    _watch_logs(caplog)
    secret = "should-not-echo-99"
    assert _run([USERNAME, secret]) == 2
    output = _captured(capsys)
    assert secret not in output
    assert secret not in caplog.text
    assert _run([USERNAME, "--password", secret]) == 2
    output = _captured(capsys)
    assert secret not in output
    assert secret not in caplog.text
    session.expire_all()
    assert session.scalars(select(StaffAccount.id)).all() == []


def test_dev_seed_flag_does_not_load_demo_accounts(
    command_db, session, monkeypatch, capsys, caplog
):
    _watch_logs(caplog)
    monkeypatch.setenv("NADOU_ALLOW_DEV_SEED", "1")
    get_settings.cache_clear()
    monkeypatch.setattr(sys, "stdin", io.StringIO(PASSWORD + "\n"))
    assert _run([USERNAME]) == 0
    assert PASSWORD not in _captured(capsys)
    assert PASSWORD not in caplog.text
    session.expire_all()
    names = set(session.scalars(select(StaffAccount.username)).all())
    assert names == {USERNAME}
    assert DemoAccounts.ADMIN not in names
    assert DemoAccounts.SUPPORT not in names


def test_module_entrypoint_requires_a_username():
    env = os.environ.copy()
    env.pop("NADOU_ALLOW_DEV_SEED", None)
    result = subprocess.run(
        [sys.executable, "-m", "nadou", "create-admin"],
        cwd=os.path.abspath(os.path.join(os.path.dirname(__file__), "..")),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    combined = result.stdout + result.stderr
    assert "username is required" in combined
    assert "create-admin" in combined
