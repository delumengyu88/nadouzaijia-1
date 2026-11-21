from datetime import timedelta

from sqlalchemy import func, select

from nadou.admin.members import EntitlementBook
from nadou.admin.phone import PhoneMask
from nadou.models import AuditLog, Entitlement
from tests.workbench_helpers import add_order, add_user, call


def test_phone_mask_keeps_last_four_digits():
    assert PhoneMask.apply("90000001234") == "*******1234"
    assert PhoneMask.apply(None) is None


def test_support_sees_masked_phone_and_cannot_mutate_users(workbench):
    user = add_user(workbench.session, workbench.clock.now, "90000001234")
    support = call(
        workbench,
        "GET",
        "/api/v1/admin/users?phone=90000001234",
        workbench.support_token,
    ).json()
    admin = call(
        workbench,
        "GET",
        f"/api/v1/admin/users/{user.id}",
        workbench.admin_token,
    ).json()
    assert support[0]["phone"] == "*******1234"
    assert admin["phone"] == "90000001234"
    masked_detail = call(
        workbench,
        "GET",
        f"/api/v1/admin/users/{user.id}",
        workbench.support_token,
    ).json()
    assert masked_detail["phone"] == "*******1234"
    user.wechat_openid = "o0000000000000a5"
    workbench.session.commit()
    listed = call(
        workbench,
        "GET",
        "/api/v1/admin/users?phone=90000001234",
        workbench.support_token,
    )
    detail = call(
        workbench,
        "GET",
        f"/api/v1/admin/users/{user.id}",
        workbench.support_token,
    )
    for response in (listed, detail):
        assert "90000001234" not in response.text
        assert "o0000000000000a5" not in response.text
    assert listed.json()[0]["phone"].endswith("1234")
    assert listed.json()[0]["wechat_openid"] is None
    assert detail.json()["wechat_openid"] is None
    assert detail.json()["phone"].endswith("1234")


def test_support_evidence_and_orders_hide_full_phone(workbench):
    phone = "13700009999"
    openid = "o-support-must-not-see"
    user = add_user(workbench.session, workbench.clock.now, phone)
    user.wechat_openid = openid
    workbench.session.commit()
    created = call(
        workbench,
        "POST",
        "/api/v1/admin/evidence-requests",
        workbench.admin_token,
        json={"user_id": user.id, "request_type": "常规", "application_number": "QZ-MASK-1"},
        expected=201,
    ).json()
    order = add_order(workbench.session, user, workbench.clock.now, "DD-MASK-1")
    support = workbench.support_token
    blobs = [
        call(workbench, "GET", "/api/v1/admin/evidence-requests", support).text,
        call(workbench, "GET", f"/api/v1/admin/evidence-requests/{created['id']}", support).text,
        call(workbench, "GET", "/api/v1/admin/orders", support).text,
        call(workbench, "GET", f"/api/v1/admin/orders/{order.id}", support).text,
    ]
    for blob in blobs:
        assert phone not in blob
        assert openid not in blob
        assert "9999" in blob
    call(
        workbench,
        "POST",
        f"/api/v1/admin/users/{user.id}/ban",
        workbench.support_token,
        json={"type": "permanent", "reason": "滥用"},
        expected=403,
    )
    call(
        workbench,
        "POST",
        f"/api/v1/admin/users/{user.id}/membership",
        workbench.support_token,
        json={"tier": "vip", "days": 30, "reason": "补偿"},
        expected=403,
    )


def test_ban_unban_and_manual_grant_are_audited(workbench):
    user = add_user(workbench.session, workbench.clock.now, "90000001235")
    until = (workbench.clock.now + timedelta(days=3)).isoformat()
    call(
        workbench,
        "POST",
        f"/api/v1/admin/users/{user.id}/ban",
        workbench.admin_token,
        json={"type": "temporary", "reason": "短期滥用"},
        expected=400,
    )
    banned = call(
        workbench,
        "POST",
        f"/api/v1/admin/users/{user.id}/ban",
        workbench.admin_token,
        json={"type": "temporary", "reason": "短期滥用", "until": until},
    ).json()
    assert banned["ban_type"] == "temporary"
    call(
        workbench,
        "POST",
        f"/api/v1/admin/users/{user.id}/unban",
        workbench.support_token,
        expected=403,
    )
    cleared = call(
        workbench,
        "POST",
        f"/api/v1/admin/users/{user.id}/unban",
        workbench.admin_token,
    ).json()
    assert cleared["ban_type"] is None
    permanent = call(
        workbench,
        "POST",
        f"/api/v1/admin/users/{user.id}/ban",
        workbench.admin_token,
        json={"type": "permanent", "reason": "严重滥用"},
    ).json()
    assert permanent["ban_type"] == "permanent"
    assert permanent["ban_until"] is None

    grant = call(
        workbench,
        "POST",
        f"/api/v1/admin/users/{user.id}/membership",
        workbench.admin_token,
        json={"tier": "vip", "days": 30, "reason": "客服补偿"},
        expected=201,
    ).json()
    assert grant["source"] == "manual"
    extended = call(
        workbench,
        "POST",
        f"/api/v1/admin/users/{user.id}/membership",
        workbench.admin_token,
        json={"tier": "vip", "days": 10, "reason": "再延长"},
        expected=201,
    ).json()
    assert extended["id"] == grant["id"]
    assert extended["source"] == "manual"
    workbench.session.expire_all()
    row = workbench.session.get(Entitlement, grant["id"])
    assert row.expires_at > workbench.clock.now + timedelta(days=39)
    assert EntitlementBook.counts_for_revenue(row.source) is False
    assert row not in EntitlementBook.revenue_entitlements(workbench.session)
    actions = set(workbench.session.scalars(select(AuditLog.action)).all())
    assert {"ban_user", "unban_user", "grant_membership"} <= actions
    assert (
        workbench.session.scalar(
            select(func.count()).select_from(Entitlement).where(Entitlement.source == "manual")
        )
        == 1
    )
