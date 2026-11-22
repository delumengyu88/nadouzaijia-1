from datetime import timedelta

import pytest
from sqlalchemy import select

from nadou.admin.audit import AuditWriter
from nadou.admin.invites import InviteRedemption
from nadou.models import Entitlement, InviteCode
from tests.workbench_helpers import add_user, call


def codes(workbench, count=2):
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
    return workbench.client.post(
        "/api/v1/invite-codes/redeem", headers={"X-User-Id": user.id}, json={"code": code}
    )


def test_redemption_errors_and_unchanged_failed_codes(workbench):
    user = add_user(workbench.session, workbench.clock.now, "90000009001")
    other = add_user(workbench.session, workbench.clock.now, "90000009002")
    rows = codes(workbench, 4)
    assert redeem(workbench, user, "BAD").json()["detail"] == "invalid"
    call(
        workbench, "POST", f"/api/v1/admin/invite-codes/{rows[0]['id']}/void", workbench.admin_token
    )
    assert redeem(workbench, user, rows[0]["code"]).json()["detail"] == "invalid"
    workbench.session.get(InviteCode, rows[1]["id"]).expires_at = workbench.clock.now
    workbench.session.commit()
    assert redeem(workbench, user, rows[1]["code"]).json()["detail"] == "expired"
    assert redeem(workbench, user, rows[2]["code"]).status_code == 200
    assert redeem(workbench, other, rows[2]["code"]).json()["detail"] == "used"
    assert redeem(workbench, user, rows[3]["code"]).json()["detail"] == "bound"
    workbench.session.expire_all()
    assert workbench.session.get(InviteCode, rows[3]["id"]).redeemed_by is None
    assert (
        len(
            workbench.session.scalars(
                select(Entitlement).where(
                    Entitlement.user_id == user.id, Entitlement.source == "invite"
                )
            ).all()
        )
        == 1
    )


def test_invite_binding_and_grant_roll_back_when_audit_fails(workbench, monkeypatch):
    user = add_user(workbench.session, workbench.clock.now, "90000009003")
    row = codes(workbench, 1)[0]

    def fail(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(AuditWriter, "write", fail)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        redeem(workbench, user, row["code"])
    workbench.session.expire_all()
    code = workbench.session.get(InviteCode, row["id"])
    assert code.redeemed_by is None and code.redeemed_at is None
    assert (
        workbench.session.scalar(select(Entitlement).where(Entitlement.user_id == user.id)) is None
    )


def test_paid_regular_and_invite_have_independent_expiry(workbench):
    user = add_user(workbench.session, workbench.clock.now, "90000009004")
    now = workbench.clock.now
    paid = Entitlement(
        user_id=user.id, kind="regular", source="order", expires_at=now + timedelta(days=60)
    )
    workbench.session.add(paid)
    workbench.session.commit()
    row = codes(workbench, 1)[0]
    result = redeem(workbench, user, row["code"])
    assert result.status_code == 200
    workbench.session.expire_all()
    assert paid.expires_at == now + timedelta(days=60)
    invite = workbench.session.get(Entitlement, result.json()["entitlement_id"])
    assert invite.expires_at == now + timedelta(days=InviteRedemption.DAYS)
