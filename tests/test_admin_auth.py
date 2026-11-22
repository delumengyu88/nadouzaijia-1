from contextlib import contextmanager
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from nadou.admin.audit import AuditWriter
from nadou.admin.auth import StaffAuthenticator, StaffDirectory, StaffSessions
from nadou.admin.errors import WorkbenchError
from nadou.config import get_settings
from nadou.models import AuditLog, StaffAccount, StaffLoginLock, StaffSession
from tests.workbench_helpers import auth, call


def test_cookie_is_httponly_and_bearer_matches_it(workbench):
    response = workbench.client.post(
        "/api/v1/admin/auth/login",
        json={"username": "wb-admin", "password": "admin-pass-1"},
    )
    assert response.status_code == 200
    cookie = response.headers["set-cookie"].lower()
    assert "nadou_admin_session=" in cookie
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    token = response.json()["token"]
    workbench.session.expire_all()
    stored = workbench.session.scalars(select(StaffSession.token_hash)).all()
    assert token not in stored
    assert StaffSessions.hash_token(token) in stored

    from fastapi.testclient import TestClient

    with TestClient(workbench.app) as bearer_client:
        fresh_bearer = bearer_client.get(
            "/api/v1/admin/auth/me",
            headers=auth(workbench.admin_token),
        )
    assert fresh_bearer.status_code == 200
    assert fresh_bearer.json()["role"] == "admin"
    with TestClient(workbench.app) as cookie_client:
        logged = cookie_client.post(
            "/api/v1/admin/auth/login",
            json={"username": "wb-support", "password": "support-pass-1"},
        )
        assert logged.status_code == 200
        cookie_client.cookies.clear()
        cookie_client.cookies.update(logged.cookies)
        as_cookie = cookie_client.get("/api/v1/admin/auth/me")
    assert as_cookie.status_code == 200
    assert as_cookie.json()["username"] == "wb-support"


def test_logout_revokes_the_session(workbench):
    from fastapi.testclient import TestClient

    with TestClient(workbench.app) as client:
        logged = client.post(
            "/api/v1/admin/auth/login",
            json={"username": "wb-support", "password": "support-pass-1"},
        )
        token = logged.json()["token"]
        gone = client.post("/api/v1/admin/auth/logout", headers=auth(token))
        assert gone.status_code == 200
        denied = client.get("/api/v1/admin/auth/me", headers=auth(token))
        assert denied.status_code == 401
    workbench.session.expire_all()
    actions = set(workbench.session.scalars(select(AuditLog.action)).all())
    assert "logout" in actions


def test_lockout_is_per_account_and_ip(session, clock):
    left = StaffDirectory.create(session, "lock-a", "lock-pass-1", "admin", clock)
    right = StaffDirectory.create(session, "lock-b", "lock-pass-1", "support", clock)
    session.commit()
    ip_a = "192.0.2.1"
    ip_b = "192.0.2.2"

    def attempt(account_name: str, password: str, ip: str, now):
        audit = AuditWriter(session, now, None, ip)
        try:
            StaffAuthenticator(session, now, audit).login(account_name, password, ip)
        except WorkbenchError as exc:
            return exc.status_code
        return 200

    for _ in range(4):
        assert attempt("lock-a", "wrong-pass", ip_a, clock) == 401
    assert attempt("lock-a", "wrong-pass", ip_a, clock) == 423
    assert attempt("lock-a", "lock-pass-1", ip_a, clock) == 423
    assert attempt("lock-a", "lock-pass-1", ip_b, clock) == 200
    assert attempt("lock-b", "lock-pass-1", ip_a, clock) == 200

    still = session.scalar(
        select(StaffLoginLock).where(
            StaffLoginLock.account_id == left.id,
            StaffLoginLock.ip == ip_a,
        )
    )
    assert still is not None
    assert still.locked_until == clock + timedelta(minutes=30)
    assert attempt("lock-a", "lock-pass-1", ip_a, clock + timedelta(minutes=29)) == 423
    assert attempt("lock-a", "lock-pass-1", ip_a, clock + timedelta(minutes=30)) == 200
    assert session.get(StaffAccount, right.id).role == "support"


def test_api_lockout_and_admin_unlock(workbench):
    for _ in range(4):
        call(
            workbench,
            "POST",
            "/api/v1/admin/auth/login",
            None,
            json={"username": "wb-support", "password": "wrong-pass"},
            expected=401,
        )
    call(
        workbench,
        "POST",
        "/api/v1/admin/auth/login",
        None,
        json={"username": "wb-support", "password": "wrong-pass"},
        expected=423,
    )
    call(
        workbench,
        "POST",
        "/api/v1/admin/auth/login",
        None,
        json={"username": "wb-admin", "password": "admin-pass-1"},
        expected=200,
    )
    locked = call(
        workbench,
        "POST",
        "/api/v1/admin/auth/login",
        None,
        json={"username": "wb-support", "password": "support-pass-1"},
        expected=423,
    )
    assert locked.json()["detail"]["message"] == "locked"
    workbench.session.expire_all()
    support = workbench.session.scalar(
        select(StaffAccount).where(StaffAccount.username == "wb-support")
    )
    call(
        workbench,
        "POST",
        f"/api/v1/admin/staff/{support.id}/unlock",
        workbench.support_token,
        expected=403,
    )
    call(
        workbench,
        "POST",
        f"/api/v1/admin/staff/{support.id}/unlock",
        workbench.admin_token,
        expected=200,
    )
    restored = call(
        workbench,
        "POST",
        "/api/v1/admin/auth/login",
        None,
        json={"username": "wb-support", "password": "support-pass-1"},
        expected=200,
    )
    assert restored.json()["role"] == "support"
    workbench.session.expire_all()
    actions = list(workbench.session.scalars(select(AuditLog.action)).all())
    assert "lock" in actions
    assert "unlock" in actions
    assert "login" in actions


class ForwardedLogin:
    @staticmethod
    @contextmanager
    def trusted_proxies(monkeypatch, value: str | None):
        if value is None:
            monkeypatch.delenv("NADOU_TRUSTED_PROXIES", raising=False)
        else:
            monkeypatch.setenv("NADOU_TRUSTED_PROXIES", value)
        get_settings.cache_clear()
        try:
            yield
        finally:
            get_settings.cache_clear()

    @staticmethod
    def post(app, peer: str, username: str, password: str, forwarded: str | None = None):
        headers = {}
        if forwarded is not None:
            headers["X-Forwarded-For"] = forwarded
        with TestClient(app, client=(peer, 50000)) as client:
            return client.post(
                "/api/v1/admin/auth/login",
                json={"username": username, "password": password},
                headers=headers,
            )

    @staticmethod
    def lock_ip(session, username: str) -> str:
        session.expire_all()
        account = session.scalar(select(StaffAccount).where(StaffAccount.username == username))
        assert account is not None
        rows = session.scalars(
            select(StaffLoginLock.ip).where(StaffLoginLock.account_id == account.id)
        ).all()
        assert len(rows) == 1
        return rows[0]


def test_spoofed_forwarded_for_keeps_the_peer(workbench, monkeypatch):
    with ForwardedLogin.trusted_proxies(monkeypatch, None):
        failed = ForwardedLogin.post(
            workbench.app,
            "203.0.113.10",
            "wb-support",
            "wrong-pass",
            "198.51.100.20",
        )
    assert failed.status_code == 401
    assert ForwardedLogin.lock_ip(workbench.session, "wb-support") == "203.0.113.10"


def test_loopback_peer_uses_forwarded_for(workbench, monkeypatch):
    with ForwardedLogin.trusted_proxies(monkeypatch, None):
        assert get_settings().trusted_proxies == "127.0.0.1"
        failed = ForwardedLogin.post(
            workbench.app,
            "127.0.0.1",
            "wb-support",
            "wrong-pass",
            "203.0.113.15",
        )
        spaced = ForwardedLogin.post(
            workbench.app,
            "127.0.0.1",
            "wb-admin",
            "wrong-pass",
            "  198.51.100.2 ,  203.0.113.16 ",
        )
    assert failed.status_code == 401
    assert spaced.status_code == 401
    assert ForwardedLogin.lock_ip(workbench.session, "wb-support") == "203.0.113.15"
    assert ForwardedLogin.lock_ip(workbench.session, "wb-admin") == "203.0.113.16"


def test_lockout_is_per_forwarded_address(workbench, monkeypatch):
    first = "203.0.113.21"
    second = "198.51.100.31"
    with ForwardedLogin.trusted_proxies(monkeypatch, None):
        for _ in range(4):
            failed = ForwardedLogin.post(
                workbench.app, "127.0.0.1", "wb-support", "wrong-pass", first
            )
            assert failed.status_code == 401
        locked = ForwardedLogin.post(workbench.app, "127.0.0.1", "wb-support", "wrong-pass", first)
        assert locked.status_code == 423
        assert ForwardedLogin.lock_ip(workbench.session, "wb-support") == first
        other = ForwardedLogin.post(
            workbench.app, "127.0.0.1", "wb-support", "support-pass-1", second
        )
        assert other.status_code == 200
        still = ForwardedLogin.post(
            workbench.app, "127.0.0.1", "wb-support", "support-pass-1", first
        )
        assert still.status_code == 423
    assert ForwardedLogin.lock_ip(workbench.session, "wb-support") == first


def test_invalid_forwarded_for_falls_back_to_peer(workbench, monkeypatch):
    with ForwardedLogin.trusted_proxies(monkeypatch, None):
        invalid = ForwardedLogin.post(
            workbench.app,
            "127.0.0.1",
            "wb-support",
            "wrong-pass",
            "not-an-ip",
        )
        left_side = ForwardedLogin.post(
            workbench.app,
            "127.0.0.1",
            "wb-admin",
            "wrong-pass",
            "203.0.113.15, not-an-ip",
        )
    assert invalid.status_code == 401
    assert left_side.status_code == 401
    assert ForwardedLogin.lock_ip(workbench.session, "wb-support") == "127.0.0.1"
    assert ForwardedLogin.lock_ip(workbench.session, "wb-admin") == "127.0.0.1"


def test_empty_trusted_proxies_keep_the_peer(workbench, monkeypatch):
    with ForwardedLogin.trusted_proxies(monkeypatch, ""):
        failed = ForwardedLogin.post(
            workbench.app,
            "127.0.0.1",
            "wb-support",
            "wrong-pass",
            "203.0.113.40",
        )
    assert failed.status_code == 401
    assert ForwardedLogin.lock_ip(workbench.session, "wb-support") == "127.0.0.1"


def test_trusted_proxy_network_uses_forwarded_for(workbench, monkeypatch):
    with ForwardedLogin.trusted_proxies(monkeypatch, "198.51.100.50,203.0.113.0/24"):
        inside = ForwardedLogin.post(
            workbench.app,
            "203.0.113.8",
            "wb-support",
            "wrong-pass",
            "198.51.100.44",
        )
        outside = ForwardedLogin.post(
            workbench.app,
            "198.51.100.8",
            "wb-admin",
            "wrong-pass",
            "203.0.113.44",
        )
    assert inside.status_code == 401
    assert outside.status_code == 401
    assert ForwardedLogin.lock_ip(workbench.session, "wb-support") == "198.51.100.44"
    assert ForwardedLogin.lock_ip(workbench.session, "wb-admin") == "198.51.100.8"
