from datetime import timedelta

from sqlalchemy import func, select

from nadou.invite_state import InviteState
from nadou.models import AuditLog, Entitlement, InviteCode, User, UserSession
from tests.workbench_helpers import add_user, call


def issue_codes(workbench, count=1):
    return call(
        workbench,
        "POST",
        "/api/v1/admin/invite-codes",
        workbench.admin_token,
        json={
            "count": count,
            "channel": "测试渠道",
            "expires_at": (workbench.clock.now + timedelta(days=1)).isoformat(),
        },
        expected=201,
    ).json()


def redeem(workbench, user, code):
    response = workbench.client.post(
        "/api/v1/invite-codes/redeem",
        headers=workbench.client.auth(user.id),
        json={"code": code},
    )
    assert response.status_code == 200, response.text
    return response


def read_state(workbench, headers=None, *, status=200):
    response = workbench.client.get("/api/v1/invite-codes/state", headers=headers or {})
    assert response.status_code == status, response.text
    if status == 200:
        assert response.headers["cache-control"] == "no-store"
    return response


def snapshot(session):
    session.rollback()
    invites = tuple(
        (
            row.id,
            row.code,
            row.channel,
            row.redeemed_by,
            row.redeemed_at,
            row.expires_at,
            row.voided_at,
            row.voided_by,
        )
        for row in session.scalars(select(InviteCode).order_by(InviteCode.id))
    )
    grants = tuple(
        (row.id, row.user_id, row.kind, row.source, row.expires_at)
        for row in session.scalars(select(Entitlement).order_by(Entitlement.id))
    )
    counts = {
        "users": session.scalar(select(func.count()).select_from(User)),
        "sessions": session.scalar(select(func.count()).select_from(UserSession)),
        "audits": session.scalar(select(func.count()).select_from(AuditLog)),
    }
    return invites, grants, counts


def test_mask_matches_the_miniprogram():
    assert InviteState.mask("") == ""
    assert InviteState.mask("ABC") == ""
    assert InviteState.mask("ABCDWXYZ") == "ABCD****"
    assert InviteState.bind_text(None) == "兑换邀请码"
    assert InviteState.bind_text("ABCDWXYZ") == "邀请码 ABCD****"


def test_missing_and_invalid_tokens_are_guests_and_write_nothing(workbench):
    user = add_user(workbench.session, workbench.clock.now, "90000009101")
    before = snapshot(workbench.session)
    headers_list = (
        {},
        {"Authorization": "Bearer not-a-session"},
        {"Authorization": "Bearer"},
        {"Authorization": "Token not-a-session"},
        {"X-User-Id": user.id},
        {"Authorization": "Bearer " + workbench.admin_token, "X-User-Id": user.id},
    )
    for headers in headers_list:
        body = read_state(workbench, headers).json()
        assert body == {"guest": True, "bound": False, "bindText": "兑换邀请码"}
        assert user.phone not in read_state(workbench, headers).text
    assert snapshot(workbench.session) == before


def test_expired_session_is_a_guest(workbench):
    user = add_user(workbench.session, workbench.clock.now, "90000009102")
    headers = workbench.client.auth(user.id)
    before = snapshot(workbench.session)
    workbench.clock.now = workbench.clock.now + timedelta(days=7)
    body = read_state(workbench, headers).json()
    assert body == {"guest": True, "bound": False, "bindText": "兑换邀请码"}
    assert snapshot(workbench.session) == before


def test_signed_in_user_without_a_code_is_unbound(workbench):
    user = add_user(workbench.session, workbench.clock.now, "90000009103")
    workbench.session.add(
        Entitlement(
            user_id=user.id,
            kind="regular",
            source="order",
            expires_at=workbench.clock.now + timedelta(days=30),
        )
    )
    workbench.session.commit()
    headers = workbench.client.auth(user.id)
    before = snapshot(workbench.session)
    body = read_state(workbench, headers).json()
    assert body == {"guest": False, "bound": False, "bindText": "兑换邀请码"}
    assert snapshot(workbench.session) == before


def test_redeemed_user_sees_only_their_masked_code(workbench):
    owner = add_user(workbench.session, workbench.clock.now, "90000009104")
    other = add_user(workbench.session, workbench.clock.now, "90000009105")
    bystander = add_user(workbench.session, workbench.clock.now, "90000009106")
    rows = issue_codes(workbench, 2)
    redeem(workbench, owner, rows[0]["code"])
    redeem(workbench, other, rows[1]["code"])
    owner_headers = workbench.client.auth(owner.id)
    other_headers = workbench.client.auth(other.id)
    bystander_headers = workbench.client.auth(bystander.id)
    before = snapshot(workbench.session)

    own = read_state(workbench, owner_headers)
    assert own.json() == {
        "guest": False,
        "bound": True,
        "bindText": "邀请码 " + rows[0]["code"][:4] + "****",
    }
    text = own.text
    assert rows[0]["code"] not in text
    assert rows[0]["code"][4:] not in text
    assert rows[1]["code"] not in text
    assert rows[1]["code"][:4] not in text
    assert "测试渠道" not in text
    assert owner.phone not in text
    assert other.phone not in text

    theirs = read_state(workbench, other_headers).json()
    assert theirs["bound"] is True
    assert theirs["bindText"] == "邀请码 " + rows[1]["code"][:4] + "****"
    assert rows[0]["code"][:4] not in theirs["bindText"]

    stranger = read_state(workbench, {**bystander_headers, "X-User-Id": owner.id})
    assert stranger.json() == {"guest": False, "bound": False, "bindText": "兑换邀请码"}
    assert rows[0]["code"] not in stranger.text
    assert rows[1]["code"][:4] not in stranger.text
    assert snapshot(workbench.session) == before


def test_expired_grant_and_expired_code_stay_bound(workbench):
    user = add_user(workbench.session, workbench.clock.now, "90000009107")
    row = issue_codes(workbench, 1)[0]
    granted = redeem(workbench, user, row["code"]).json()
    code = workbench.session.get(InviteCode, row["id"])
    grant = workbench.session.get(Entitlement, granted["entitlement_id"])
    code.expires_at = workbench.clock.now
    grant.expires_at = workbench.clock.now
    workbench.session.commit()
    headers = workbench.client.auth(user.id)
    me = workbench.client.get("/api/v1/auth/me", headers=headers)
    assert me.json()["tier"] == "free"
    before = snapshot(workbench.session)
    body = read_state(workbench, headers).json()
    assert body == {
        "guest": False,
        "bound": True,
        "bindText": "邀请码 " + row["code"][:4] + "****",
    }
    assert row["code"] not in str(body)
    assert snapshot(workbench.session) == before


def test_banned_account_matches_auth_me(workbench):
    user = add_user(workbench.session, workbench.clock.now, "90000009108")
    headers = workbench.client.auth(user.id)
    user.ban_type = "permanent"
    workbench.session.commit()
    before = snapshot(workbench.session)
    response = read_state(workbench, headers, status=403)
    assert response.json()["detail"] == "account is disabled"
    assert (
        workbench.client.get("/api/v1/auth/me", headers=headers).status_code == response.status_code
    )
    assert snapshot(workbench.session) == before
