from datetime import timedelta

from sqlalchemy import func, select
from tests.workbench_helpers import add_user, call

from nadou.admin.members import EntitlementBook
from nadou.admin.phone import PhoneMask
from nadou.models import AuditLog, Entitlement


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
