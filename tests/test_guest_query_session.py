from datetime import timedelta
from unittest.mock import Mock

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from nadou import auth
from nadou.auth import GuestIssueLimit, UserSessions
from nadou.db import Database
from nadou.main import create_app
from nadou.models import Entitlement, Order, User, UserSession
from tests.test_query_export import add_record

ISSUE = "/api/v1/auth/guest/session"
ME = "/api/v1/auth/me"
HISTORY = "/api/v1/queries"


def bearer(body):
    return {"Authorization": "Bearer " + body["token"]}


def test_guest_queries_are_separate_from_other_guests_and_existing_accounts(
    session, clock, monkeypatch
):
    monkeypatch.setenv("NADOU_DATA_SOURCE", "export")
    auth.get_settings.cache_clear()
    exchange = Mock(side_effect=AssertionError("guests do not exchange WeChat codes"))
    monkeypatch.setattr(auth.WeChatLogin, "exchange", exchange)
    db = Database(engine=session.get_bind())
    client = TestClient(create_app(database=db, now=lambda: clock))
    member = User(id="existing", created_at=clock)
    session.add(member)
    session.flush()
    member_token = UserSessions.issue(session, member, clock)
    session.commit()
    linked = bearer({"token": member_token})
    assert client.post(ISSUE, json={"user_id": member.id}).status_code == 422
    assert client.post(ISSUE, json={}, headers=linked).status_code == 409
    first = client.post(ISSUE, json={})
    assert first.status_code == 200 and first.headers["Cache-Control"] == "no-store"
    one, two = first.json(), client.post(ISSUE, json={}).json()
    assert one["user"]["identity_kind"] == two["user"]["identity_kind"] == "guest"
    assert len({member.id, one["user"]["id"], two["user"]["id"]}) == 3
    assert one["token"] != two["token"] and len(one["token"]) >= 43
    a, b = bearer(one), bearer(two)
    retry = client.post(ISSUE, json={}, headers=a)
    assert retry.status_code == 200 and retry.json() == one
    assert session.scalar(select(func.count()).select_from(UserSession)) == 3
    assert all(s.token_hash != one["token"] for s in session.scalars(select(UserSession)))
    title = "本次游客节目"
    own = client.post(HISTORY, json={"name": title}, headers=a)
    assert own.status_code == 201
    path = HISTORY + "/" + own.json()["id"]
    assert client.post(path + "/results", json={"name": title}, headers=a).json()["items"][0][
        "unverified"
    ]
    assert (
        client.get(path, headers=a).status_code
        == client.post(path + "/seen", headers=a).status_code
        == 200
    )
    for other in (b, linked):
        assert client.get(HISTORY, headers=other).json()["items"] == []
        assert client.get(path, headers=other).status_code == 404
        assert (
            client.post(path + "/results", json={"name": title}, headers=other).status_code == 404
        )
        assert client.post(path + "/seen", headers=other).status_code == 404
    answer = client.get("/api/v1/programs/search", params={"name": title}, headers=a).json()
    assert answer["outcome"] == "unverified" and answer["locked_summary"]["unlockable_total"] == 0
    day8 = TestClient(create_app(database=db, now=lambda: clock + timedelta(days=8)))
    assert day8.get(path, headers=a).status_code == 200
    assert day8.get(path, headers=b).status_code == 404
    assert day8.get(ME, headers=linked).status_code == 401
    next_query = day8.post(HISTORY, json={"name": "重访时查询"}, headers=a)
    assert next_query.status_code == 201
    until = clock + timedelta(days=38)
    assert next_query.headers["X-Nadou-Session-Expires-At"] == until.isoformat()
    delayed = TestClient(create_app(database=db, now=lambda: clock + timedelta(days=7)))
    assert delayed.post(ISSUE, json={}, headers=a).json()["expires_at"] == until.isoformat()
    next_path = HISTORY + "/" + next_query.json()["id"]
    last = TestClient(create_app(database=db, now=lambda: until - timedelta(seconds=1)))
    assert last.get(next_path, headers=a).status_code == 200
    assert last.post(HISTORY, json={"name": "  "}, headers=a).status_code == 400
    assert last.get(next_path, headers=b).status_code == 401
    later = TestClient(create_app(database=db, now=lambda: until))
    for endpoint in (HISTORY, ME):
        assert later.get(endpoint, headers=a).status_code == 401
    assert later.post(ISSUE, json={}, headers=a).status_code == 401
    assert session.scalar(select(func.count()).select_from(User)) == 3
    new = later.post(ISSUE, json={}).json()
    assert (
        new["user"]["id"] != one["user"]["id"]
        and later.get(path, headers=bearer(new)).status_code == 404
    )
    exchange.assert_not_called()


def test_guest_is_free_even_with_a_stray_grant_or_allowlist(session, clock, monkeypatch):
    db = Database(engine=session.get_bind())
    client = TestClient(create_app(database=db, now=lambda: clock))
    guest = client.post(ISSUE, json={}).json()
    uid = guest["user"]["id"]
    headers = bearer(guest)
    monkeypatch.setenv("NADOU_TRIAL_TOOLS", "1")
    monkeypatch.setenv("NADOU_WECHAT_PAY_TEST_TIER", "1")
    monkeypatch.setenv("NADOU_WECHAT_PAY_TEST_USERS", uid)
    auth.get_settings.cache_clear()
    session.add(Entitlement(user_id=uid, kind="vip", expires_at=clock + timedelta(days=3)))
    add_record(
        session,
        task_id="q-task",
        record_id="q-record",
        program="认证节目",
        when=clock - timedelta(days=40),
    )
    session.commit()
    assert client.get(ME, headers=headers).json() == guest["user"]
    result = client.get(
        "/api/v1/programs/search", params={"name": "认证节目"}, headers=headers
    ).json()
    assert result["outcome"] == "hit" and result["locked_records"] and not result["platform_groups"]
    created = client.post(HISTORY, json={"name": "认证节目"}, headers=headers).json()
    export = HISTORY + "/" + created["id"]
    detail = client.post(export + "/results", json={"name": "认证节目"}, headers=headers).json()
    assert detail["items"][0]["evidenceName"] is None
    for method, path, body in (
        ("get", export + "/export", None),
        ("get", export + "/program-export?program_name=x", None),
        ("get", "/api/v1/pay/orders", None),
        ("get", "/api/v1/pay/tiers?channel=virtual", None),
        ("post", "/api/v1/pay/orders", {"tier": "regular"}),
        ("post", "/api/v1/xpay/orders", {"tier": "regular", "code": "synthetic"}),
        ("get", "/api/v1/pay/orders/unknown", None),
        ("get", "/api/v1/xpay/orders/unknown", None),
        ("post", "/api/v1/xpay/orders/unknown/sync", {}),
        ("get", "/api/v1/evidence-requests", None),
        ("post", "/api/v1/invite-codes/redeem", {"code": "test"}),
        ("get", "/api/v1/invite-codes/state", None),
    ):
        assert client.request(method, path, headers=headers, json=body).status_code == 403, path
    assert client.post("/api/v1/dev/tier", headers=headers, json={"tier": "vip"}).status_code == 404
    assert session.scalar(select(func.count()).select_from(Order)) == 0


def test_guest_issue_limit_only_applies_to_new_identities(session, clock, monkeypatch):
    from nadou import main

    monkeypatch.setattr(auth, "monotonic", lambda: 100.0)
    monkeypatch.setattr(main, "GuestIssueLimit", lambda: GuestIssueLimit(burst=1, per_second=1))
    client = TestClient(create_app(database=Database(engine=session.get_bind()), now=lambda: clock))
    own = client.post(ISSUE, json={}).json()
    full = client.post(ISSUE, json={}, headers={"X-Forwarded-For": "ignored"})
    assert full.status_code == 429 and full.headers["Retry-After"] == "1"
    assert client.get(ME, headers=bearer(own)).status_code == 200
    assert client.post(ISSUE, json={}, headers=bearer(own)).json() == own
    assert session.scalar(select(func.count()).select_from(User)) == 1
    monkeypatch.setattr(auth, "monotonic", lambda: 101.0)
    new = client.post(ISSUE, json={})
    assert new.status_code == 200 and new.json()["user"]["id"] != own["user"]["id"]
