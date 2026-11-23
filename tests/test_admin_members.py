from datetime import datetime, timedelta

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
    assert support["items"][0]["phone"] == "*******1234"
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
    assert listed.json()["items"][0]["phone"].endswith("1234")
    assert listed.json()["items"][0]["wechat_openid"] is None
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


def test_lists_recent_users_without_query(workbench):
    earlier = workbench.clock.now - timedelta(days=2)
    later = workbench.clock.now - timedelta(hours=1)
    older = add_user(workbench.session, earlier, "90000002001")
    older.display_name = "旧用户"
    newer = add_user(workbench.session, later, None)
    newer.display_name = "新用户"
    newer.wechat_openid = "o-newest-user"
    workbench.session.commit()
    body = call(workbench, "GET", "/api/v1/admin/users", workbench.admin_token).json()
    assert body["total"] == 2
    assert body["page"] == 1
    assert body["page_size"] == 20
    assert [row["id"] for row in body["items"]] == [newer.id, older.id]
    assert body["items"][0]["display_name"] == "新用户"
    assert body["items"][0]["phone"] is None
    assert body["items"][0]["wechat_openid"] == "o-newest-user"


def test_user_list_pagination(workbench):
    now = workbench.clock.now
    created = []
    for index in range(25):
        user = add_user(workbench.session, now - timedelta(minutes=index), f"90000003{index:03d}")
        user.display_name = f"用户{index}"
        created.append(user)
    workbench.session.commit()
    first = call(
        workbench,
        "GET",
        "/api/v1/admin/users?page=1&page_size=20",
        workbench.admin_token,
    ).json()
    second = call(
        workbench,
        "GET",
        "/api/v1/admin/users?page=2&page_size=20",
        workbench.admin_token,
    ).json()
    assert first["total"] == 25
    assert len(first["items"]) == 20
    assert first["items"][0]["id"] == created[0].id
    assert second["page"] == 2
    assert [row["id"] for row in second["items"]] == [row.id for row in created[20:]]
    oversized = call(
        workbench,
        "GET",
        "/api/v1/admin/users?page_size=101",
        workbench.admin_token,
        expected=422,
    )
    assert oversized.status_code == 422


def test_user_list_fuzzy_search(workbench):
    now = workbench.clock.now
    named = add_user(workbench.session, now - timedelta(days=3), "13700001111")
    named.display_name = "何帆工作室"
    openid = add_user(workbench.session, now - timedelta(days=2), None)
    openid.display_name = "微信用户"
    openid.wechat_openid = "oPrefixUserAAA"
    other = add_user(workbench.session, now - timedelta(days=1), "13900002222")
    other.display_name = "林秋"
    wildcard = add_user(workbench.session, now, "13600003333")
    wildcard.display_name = "百分百"
    workbench.session.commit()
    token = workbench.admin_token

    by_phone = call(workbench, "GET", "/api/v1/admin/users?q=000011", token).json()
    assert [row["id"] for row in by_phone["items"]] == [named.id]
    assert by_phone["total"] == 1

    by_name = call(workbench, "GET", "/api/v1/admin/users?q=何帆", token).json()
    assert [row["id"] for row in by_name["items"]] == [named.id]

    by_openid = call(workbench, "GET", "/api/v1/admin/users?q=oPrefixUser", token).json()
    assert [row["id"] for row in by_openid["items"]] == [openid.id]
    exact_openid = call(workbench, "GET", "/api/v1/admin/users?q=oPrefixUserAAA", token).json()
    assert [row["id"] for row in exact_openid["items"]] == [openid.id]

    by_id = call(workbench, "GET", f"/api/v1/admin/users?q={other.id}", token).json()
    assert [row["id"] for row in by_id["items"]] == [other.id]

    escaped = call(workbench, "GET", "/api/v1/admin/users?q=%", token).json()
    assert escaped["total"] == 0
    underscore = call(workbench, "GET", "/api/v1/admin/users?q=_", token).json()
    assert underscore["total"] == 0


def test_support_list_masks_phone_without_query(workbench):
    user = add_user(workbench.session, workbench.clock.now, "90000004444")
    user.wechat_openid = "o-hidden-openid"
    workbench.session.commit()
    listed = call(workbench, "GET", "/api/v1/admin/users", workbench.support_token)
    row = listed.json()["items"][0]
    assert row["phone"] == "*******4444"
    assert row["wechat_openid"] is None
    assert "90000004444" not in listed.text
    assert "o-hidden-openid" not in listed.text
    admin = call(workbench, "GET", "/api/v1/admin/users", workbench.admin_token).json()["items"][0]
    assert admin["phone"] == "90000004444"
    assert admin["wechat_openid"] == "o-hidden-openid"


def test_user_list_includes_active_membership(workbench):
    now = workbench.clock.now
    vip = add_user(workbench.session, now, "90000005001", vip=True)
    vip.created_at = now - timedelta(days=3)
    both = add_user(workbench.session, now, "90000005002", vip=True)
    both.created_at = now - timedelta(days=2)
    workbench.session.add(
        Entitlement(
            user_id=both.id,
            kind="regular",
            program_name=None,
            record_id=None,
            expires_at=now + timedelta(days=90),
            source="invite",
        )
    )
    regular = add_user(workbench.session, now, "90000005003")
    regular.created_at = now - timedelta(days=1)
    workbench.session.add(
        Entitlement(
            user_id=regular.id,
            kind="regular",
            program_name=None,
            record_id=None,
            expires_at=now + timedelta(days=10),
            source="order",
        )
    )
    expired = add_user(workbench.session, now, "90000005004")
    workbench.session.add(
        Entitlement(
            user_id=expired.id,
            kind="vip",
            program_name=None,
            record_id=None,
            expires_at=now - timedelta(days=1),
            source="order",
        )
    )
    workbench.session.commit()
    listed = call(workbench, "GET", "/api/v1/admin/users", workbench.admin_token).json()
    rows = {row["id"]: row for row in listed["items"]}

    def expiry(row):
        return datetime.fromisoformat(row["membership_expires_at"])

    assert rows[vip.id]["membership"] == "vip"
    assert expiry(rows[vip.id]) == now + timedelta(days=30)
    assert rows[both.id]["membership"] == "vip"
    assert expiry(rows[both.id]) == now + timedelta(days=30)
    assert rows[regular.id]["membership"] == "regular"
    assert expiry(rows[regular.id]) == now + timedelta(days=10)
    assert rows[expired.id]["membership"] is None
    assert rows[expired.id]["membership_expires_at"] is None
