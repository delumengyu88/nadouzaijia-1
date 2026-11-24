"""Payment callbacks racing other requests must not freeze the API (2026-11-24 incident).

The xpay notify route used to be ``async def`` and took the owner-row
``FOR UPDATE`` on the event loop. When ``/sync`` (a threadpool route) held
that lock until its dependency teardown, the teardown needed the event loop
and the loop was waiting for the lock. Postgres sees no cycle, so the API
froze. These tests need Postgres (``DATABASE_URL``) and use a throwaway schema.
"""

import os
import secrets
import threading
import time

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from nadou.db import Base, Database
from nadou.main import create_app
from nadou.models import Entitlement, Order, OrderIssuance, User
from nadou.payments import xpay, xpay_api
from tests.test_virtual_pay import configure_diamond, payment, signed_event
from tests.user_helpers import UserClient

pytestmark = pytest.mark.postgres

APP_NAME = "nadou_paylock_test"
WATCHDOG_SECONDS = 15


def _url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    if not url.startswith("postgresql"):
        if os.environ.get("NADOU_REQUIRE_POSTGRES") == "1":
            pytest.fail("DATABASE_URL must point at Postgres")
        pytest.skip("DATABASE_URL is not set to Postgres")
    return url


@pytest.fixture
def pg(monkeypatch):
    url = _url()
    schema = "paylock_" + secrets.token_hex(4)
    admin = create_engine(url)
    with admin.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA {schema}"))
    engine = create_engine(
        url,
        pool_pre_ping=True,
        connect_args={"options": f"-c search_path={schema}", "application_name": APP_NAME},
    )
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as conn:
            conn.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        admin.dispose()


@pytest.fixture
def paying(pg, clock, monkeypatch):
    import base64

    from pydantic import SecretStr

    from nadou.auth import WeChatLogin
    from nadou.config import get_settings

    cfg = get_settings()
    monkeypatch.setattr(cfg, "xpay_env", 0)
    monkeypatch.setattr(cfg, "xpay_offer", "offer-test")
    monkeypatch.setattr(cfg, "xpay_live_key", SecretStr(secrets.token_hex(16)))
    monkeypatch.setattr(cfg, "xpay_push_token", SecretStr(secrets.token_hex(16)))
    monkeypatch.setattr(
        cfg,
        "xpay_push_aes",
        SecretStr(base64.b64encode(secrets.token_bytes(32)).decode().rstrip("=")),
    )
    configure_diamond(monkeypatch)
    session = sessionmaker(bind=pg, expire_on_commit=False)()
    app = create_app(database=Database(engine=pg), now=lambda: clock)
    # One TestClient context = one shared event loop, like one uvicorn worker.
    with UserClient(app, session, clock, default_user=True) as client:
        user = session.get(User, "test-default-free")
        user.wechat_openid = "virtual-owner"
        session.commit()
        monkeypatch.setattr(
            WeChatLogin,
            "exchange_session",
            staticmethod(lambda _: ("virtual-owner", secrets.token_hex(16))),
        )
        monkeypatch.setattr(xpay, "provider_call", lambda *_: {})
        result = client.post(
            "/api/v1/xpay/orders", json={"tier": "membership_diamond", "code": "c"}
        )
        assert result.status_code == 200, result.text
        number = result.json()["order_number"]
        yield client, session, number
    session.close()


def delivered(number):
    return {
        "Event": "xpay_goods_deliver_notify",
        "OpenId": "virtual-owner",
        "Env": 0,
        "OutTradeNo": number,
        "GoodsInfo": {
            "ProductId": "official-product",
            "Quantity": 1,
            "ActualPrice": 100,
            "OrigPrice": 100,
            "Attach": "membership_diamond",
        },
    }


def run(target, results, key):
    def body():
        try:
            results[key] = target()
        except BaseException as exc:  # recorded and asserted below
            results[key] = exc

    thread = threading.Thread(target=body, daemon=True)
    thread.start()
    return thread


def cancel_lock_waiters(engine):
    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT count(pg_cancel_backend(pid)) FROM pg_stat_activity "
                "WHERE application_name = :app AND wait_event_type = 'Lock'"
            ),
            {"app": APP_NAME},
        ).scalar()


def join_or_unfreeze(engine, threads):
    deadline = time.monotonic() + WATCHDOG_SECONDS
    for thread in threads:
        thread.join(max(0.0, deadline - time.monotonic()))
    frozen = any(t.is_alive() for t in threads)
    if frozen:
        with engine.connect() as conn:
            for row in conn.execute(
                text(
                    "SELECT pid, state, wait_event_type, left(query, 120) FROM pg_stat_activity "
                    "WHERE application_name = :app"
                ),
                {"app": APP_NAME},
            ):
                print("PGACT", tuple(row))
        cancelled = cancel_lock_waiters(engine)
        for thread in threads:
            thread.join(10)
        pytest.fail(f"API froze: requests still waiting after {WATCHDOG_SECONDS}s ({cancelled=})")


def counts(session):
    session.expire_all()
    return session.query(OrderIssuance).count(), session.query(Entitlement).count()


def test_sync_holding_owner_lock_and_notify_do_not_freeze(paying, pg, monkeypatch):
    """Replays 10:37:08: client polls /sync while WeChat posts the delivery notify."""
    client, session, number = paying
    sync_locked = threading.Event()
    notify_locking = threading.Event()
    original = xpay_api.lock
    calls = []

    def lock(db, order, virtual):
        calls.append(threading.current_thread().name)
        first = len(calls) == 1
        if not first:
            notify_locking.set()
        original(db, order, virtual)
        if first:
            sync_locked.set()
            # Keep the sync request inside its transaction until notify waits on the row.
            notify_locking.wait(10)
            time.sleep(0.5)

    statuses = iter([1])  # sync sees "paid, not yet delivered"; notify sees status 2
    monkeypatch.setattr(xpay_api, "lock", lock)
    monkeypatch.setattr(
        xpay,
        "query_order",
        lambda env, openid, no: payment(no, status=next(statuses, 2), price=100),
    )
    results = {}
    sync = run(lambda: client.post(f"/api/v1/xpay/orders/{number}/sync"), results, "sync")
    assert sync_locked.wait(10)
    notify = run(lambda: signed_event(client, delivered(number), monkeypatch), results, "notify")
    join_or_unfreeze(pg, [sync, notify])
    assert results["sync"].status_code == 200, results["sync"].text
    assert results["notify"].text == "success", results["notify"].text
    assert client.get("/health").status_code == 200
    assert counts(session) == (1, 1)
    session.expire_all()
    assert session.query(Order).filter_by(order_number=number).one().status == "已发放"


def test_notify_waiting_on_lock_keeps_other_requests_served_and_is_idempotent(
    paying, pg, monkeypatch
):
    client, session, number = paying
    monkeypatch.setattr(
        xpay, "query_order", lambda env, openid, no: payment(no, status=2, price=100)
    )
    blocker = pg.connect()
    tx = blocker.begin()
    blocker.execute(text("SELECT id FROM users WHERE id = 'test-default-free' FOR UPDATE"))
    results = {}
    try:
        notify = run(lambda: signed_event(client, delivered(number), monkeypatch), results, "n1")
        time.sleep(1.0)  # notify is now waiting for the owner row
        started = time.monotonic()
        health = run(lambda: client.get("/health"), results, "health")
        health.join(3)
        assert not health.is_alive(), "event loop blocked by a payment lock wait"
        assert results["health"].status_code == 200
        assert time.monotonic() - started < 3
    finally:
        tx.rollback()
        blocker.close()
    join_or_unfreeze(pg, [notify])
    assert results["n1"].text == "success"
    # WeChat retries the same delivery: still one issuance and one entitlement.
    assert signed_event(client, delivered(number), monkeypatch).text == "success"
    assert counts(session) == (1, 1)


def test_notify_gives_up_on_a_stuck_lock_with_retryable_503(paying, pg, monkeypatch):
    client, session, number = paying
    monkeypatch.setattr(
        xpay, "query_order", lambda env, openid, no: payment(no, status=2, price=100)
    )
    blocker = pg.connect()
    tx = blocker.begin()
    blocker.execute(text("SELECT id FROM users WHERE id = 'test-default-free' FOR UPDATE"))
    results = {}
    try:
        notify = run(lambda: signed_event(client, delivered(number), monkeypatch), results, "n")
        notify.join(WATCHDOG_SECONDS)
        alive = notify.is_alive()
    finally:
        tx.rollback()
        blocker.close()
    join_or_unfreeze(pg, [notify])
    assert not alive, "lock wait is unbounded"
    assert results["n"].status_code == 503
    assert counts(session) == (0, 0)
    assert signed_event(client, delivered(number), monkeypatch).text == "success"
    assert counts(session) == (1, 1)
