from datetime import timedelta
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from nadou.config import get_settings
from nadou.db import Database
from nadou.main import create_app
from nadou.models import User


@pytest.fixture
def login_env(monkeypatch):
    monkeypatch.setenv("NADOU_WX_APPID", "test-app-id")
    monkeypatch.setenv("NADOU_WX_APPSECRET", "test-app-secret")
    monkeypatch.setenv("NADOU_ALLOW_DEV_SEED", "0")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def anonymous(session, clock, login_env):
    return TestClient(create_app(database=Database(engine=session.get_bind()), now=lambda: clock))


def wechat(monkeypatch, body=None, error=None, status=200):
    response = httpx.Response(
        status,
        json=body,
        request=httpx.Request("GET", "https://api.weixin.qq.com/sns/jscode2session"),
    )
    get = Mock(side_effect=error, return_value=response)
    monkeypatch.setattr(httpx, "get", get)
    return get


def login(client, code="test-code"):
    return client.post("/api/v1/auth/wechat/login", json={"code": code})


def bearer(token):
    return {"Authorization": "Bearer " + token}


def test_wechat_login_creates_user_reuses_openid_and_issues_private_session(
    anonymous, session, clock, monkeypatch
):
    upstream = wechat(monkeypatch, {"openid": "test-openid", "session_key": "test-wx-key"})
    first = login(anonymous)
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["token_type"] == "bearer"
    assert body["expires_at"] == (clock + timedelta(days=7)).isoformat()
    assert body["user"]["tier"] == "free"
    token = body["token"]
    assert len(token) >= 43
    assert "test-wx-key" not in first.text and "test-openid" not in first.text
    upstream.assert_called_once_with(
        "https://api.weixin.qq.com/sns/jscode2session",
        params={
            "appid": "test-app-id",
            "secret": "test-app-secret",
            "js_code": "test-code",
            "grant_type": "authorization_code",
        },
        timeout=10,
        follow_redirects=False,
    )
    me = anonymous.get("/api/v1/auth/me", headers=bearer(token))
    assert me.status_code == 200
    assert me.json()["id"] == body["user"]["id"]
    assert (
        anonymous.get(
            "/api/v1/programs/search", params={"name": "测试节目"}, headers=bearer(token)
        ).status_code
        == 200
    )
    again = login(anonymous, "second-code").json()
    assert again["user"]["id"] == body["user"]["id"]
    assert again["token"] != token
    assert session.scalar(select(func.count()).select_from(User)) == 1
    from nadou.models import UserSession

    rows = session.scalars(select(UserSession)).all()
    assert len(rows) == 2
    assert all(row.token_hash != token and row.expires_at > clock for row in rows)


@pytest.mark.parametrize(
    "body,status",
    [
        ({"errcode": 40029, "errmsg": "invalid code"}, 401),
        ({"errcode": 40163}, 401),
        ({"errcode": -1}, 503),
        ({"errcode": 45011}, 503),
        ({}, 503),
        ({"openid": "", "session_key": "test-key"}, 503),
        ({"openid": "test-openid"}, 503),
        ([], 503),
    ],
)
def test_wechat_failure_never_creates_a_user(anonymous, session, monkeypatch, body, status):
    wechat(monkeypatch, body)
    result = login(anonymous)
    assert result.status_code == status
    assert session.scalar(select(func.count()).select_from(User)) == 0
    assert "test-app-secret" not in result.text


@pytest.mark.parametrize("failure", ["timeout", "http", "json"])
def test_upstream_failure_is_sanitized(anonymous, monkeypatch, failure):
    if failure == "timeout":
        wechat(monkeypatch, error=httpx.ReadTimeout("test-app-secret"))
    elif failure == "http":
        wechat(monkeypatch, {"secret": "test-app-secret"}, status=500)
    else:
        get = wechat(monkeypatch)
        get.return_value.json = Mock(side_effect=ValueError("test-app-secret"))
    response = login(anonymous)
    assert response.status_code == 503
    assert "test-app-secret" not in response.text


@pytest.mark.parametrize("code", ["", "   ", "x" * 513])
def test_invalid_code_is_rejected_before_wechat(anonymous, monkeypatch, code):
    get = wechat(monkeypatch)
    assert login(anonymous, code).status_code == 422
    get.assert_not_called()


def test_missing_configuration_fails_closed(anonymous, monkeypatch):
    monkeypatch.setenv("NADOU_WX_APPID", "")
    monkeypatch.setenv("NADOU_WX_APPSECRET", "")
    get_settings.cache_clear()
    get = wechat(monkeypatch)
    assert login(anonymous).status_code == 503
    get.assert_not_called()


@pytest.mark.parametrize(
    "path,method,payload",
    [
        ("/api/v1/programs/search?name=测试节目", "GET", None),
        ("/api/v1/queries/any/export", "GET", None),
        ("/api/v1/invite-codes/redeem", "POST", {"code": "A8K3N2P7"}),
        ("/api/v1/auth/me", "GET", None),
    ],
)
@pytest.mark.parametrize("dev_seed", ["0", "1"])
def test_no_session_and_legacy_header_cannot_authenticate(
    anonymous, session, clock, monkeypatch, path, method, payload, dev_seed
):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("NADOU_ALLOW_DEV_SEED", dev_seed)
    get_settings.cache_clear()
    session.add(User(id="victim", created_at=clock))
    session.commit()
    for headers in (
        {},
        {"X-User-Id": "victim"},
        {"Authorization": "Bearer invalid", "X-User-Id": "victim"},
    ):
        response = anonymous.request(method, path, headers=headers, json=payload)
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"


def test_expired_session_is_rejected_at_expiry(anonymous, session, clock, monkeypatch):
    wechat(monkeypatch, {"openid": "expiry-openid", "session_key": "test-key"})
    token = login(anonymous).json()["token"]
    later = TestClient(
        create_app(
            database=Database(engine=session.get_bind()), now=lambda: clock + timedelta(days=7)
        )
    )
    assert later.get("/api/v1/auth/me", headers=bearer(token)).status_code == 401
    assert later.get("/api/v1/queries/any/export", headers=bearer(token)).status_code == 401


def test_legacy_header_cannot_override_valid_session(anonymous, session, clock, monkeypatch):
    wechat(monkeypatch, {"openid": "real-openid", "session_key": "test-key"})
    body = login(anonymous).json()
    session.add(User(id="victim", created_at=clock))
    session.commit()
    headers = {**bearer(body["token"]), "X-User-Id": "victim"}
    assert anonymous.get("/api/v1/auth/me", headers=headers).json()["id"] == body["user"]["id"]
    assert anonymous.get("/api/v1/admin/users?phone=x", headers=headers).status_code == 401


def test_banned_user_cannot_login_or_use_existing_session(anonymous, session, clock, monkeypatch):
    wechat(monkeypatch, {"openid": "ban-openid", "session_key": "test-key"})
    body = login(anonymous).json()
    user = session.get(User, body["user"]["id"])
    user.ban_type = "permanent"
    session.commit()
    assert login(anonymous).status_code == 403
    assert anonymous.get("/api/v1/auth/me", headers=bearer(body["token"])).status_code == 403


def test_login_session_is_committed_before_response(anonymous, session, clock, monkeypatch):
    from sqlalchemy.orm import Session

    from nadou.auth import UserSessions
    from tests.test_response_transactions import ObserveResponse

    wechat(monkeypatch, {"openid": "committed-openid", "session_key": "test-key"})
    visible = []

    def observe(body):
        with Session(session.get_bind()) as reader:
            user = UserSessions.resolve(reader, "Bearer " + body["token"], clock)
            visible.append(user.id)

    client = TestClient(ObserveResponse(anonymous.app, observe))
    response = login(client)
    assert response.status_code == 200
    assert visible == [response.json()["user"]["id"]]


def test_profile_reports_current_server_memberships(anonymous, session, clock, monkeypatch):
    from nadou.models import Entitlement

    wechat(monkeypatch, {"openid": "member-openid", "session_key": "test-key"})
    body = login(anonymous).json()
    for kind, end in [("vip", clock + timedelta(hours=1)), ("regular", clock + timedelta(days=2))]:
        session.add(Entitlement(user_id=body["user"]["id"], kind=kind, expires_at=end))
    session.commit()
    profile = anonymous.get("/api/v1/auth/me", headers=bearer(body["token"])).json()
    assert profile["tier"] == "vip"
    assert profile["vip_expires_at"] is not None and profile["regular_expires_at"] is not None
    later = TestClient(
        create_app(
            database=Database(engine=session.get_bind()), now=lambda: clock + timedelta(hours=1)
        )
    )
    assert later.get("/api/v1/auth/me", headers=bearer(body["token"])).json()["tier"] == "regular"


def test_staff_token_cannot_authenticate_user_routes(workbench):
    response = workbench.client.get("/api/v1/auth/me", headers=bearer(workbench.admin_token))
    assert response.status_code == 401
