import re
from datetime import timedelta

from sqlalchemy import select

from nadou.admin.demo_data import DemoAccounts, DemoData
from nadou.admin.invites import InviteAlphabet
from nadou.main import AdminUi
from nadou.models import AuditLog, Entitlement
from tests.workbench_helpers import add_order, add_user, call

BANNED = (
    "待确认",
    "疑似命中",
    "确认命中",
    "别名",
    "匹配方式",
    "命中类型",
    "顺延",
    "VIP 结束后生效",
    "类别",
    "年份",
)


def test_served_ui_has_no_banned_labels():
    root = AdminUi.root()
    text = "\n".join(path.read_text(encoding="utf-8") for path in root.glob("*"))
    for word in BANNED:
        assert word not in text
    assert "客服消息" in text
    assert "平台未识别 ?" in text
    assert "已命中" in text
    assert "同时计时" not in text
    assert "普通会员 至 " in text
    assert "生成规则" not in text
    assert "已通知管理员" in text


def test_demo_pages_and_api_hide_banned_labels(workbench):
    DemoData.load(workbench.session, workbench.clock.now)
    workbench.session.commit()
    token = workbench.admin_token
    blobs = []
    for path in (
        "/api/v1/admin/evidence-requests",
        "/api/v1/admin/invite-codes",
        "/api/v1/admin/invite-codes/stats",
        "/api/v1/admin/orders",
        "/api/v1/admin/audit-logs?limit=200",
        "/api/v1/admin/index/status",
        "/api/v1/admin/content/splash",
        "/api/v1/admin/content/announcements",
        "/api/v1/admin/content/examples",
        "/api/v1/admin/staff/locks",
    ):
        blobs.append(call(workbench, "GET", path, token).text)
    user = call(
        workbench,
        "GET",
        f"/api/v1/admin/users?q={DemoAccounts.SAMPLE_PHONE}",
        token,
    ).json()
    detail = call(workbench, "GET", f"/api/v1/admin/users/{user[0]['id']}", token).text
    blobs.append(detail)
    search = workbench.client.get(
        "/api/v1/programs/search",
        params={"name": "示例节目"},
        headers=workbench.client.auth(user[0]["id"]),
    )
    blobs.append(search.text)
    joined = "\n".join(blobs)
    for word in BANNED:
        assert word not in joined
    assert "已命中" in search.text
    assert "平台未识别" in joined
    page = workbench.client.get("/admin/")
    assert page.status_code == 200
    assert "那豆在架" in page.text
    script = workbench.client.get("/admin/app.js")
    assert script.status_code == 200


def test_evidence_membership_reissue_and_ban_are_audited(workbench):
    user = add_user(workbench.session, workbench.clock.now, "90000008001", vip=True)
    created = call(
        workbench,
        "POST",
        "/api/v1/admin/evidence-requests",
        workbench.admin_token,
        json={"user_id": user.id, "request_type": "常规", "application_number": "QZ-AUDIT-1"},
        expected=201,
    ).json()
    request_id = created["id"]
    call(
        workbench,
        "POST",
        f"/api/v1/admin/evidence-requests/{request_id}/accept",
        workbench.admin_token,
    )
    call(
        workbench,
        "POST",
        f"/api/v1/admin/evidence-requests/{request_id}/quote",
        workbench.admin_token,
        json={"amount": 100},
    )
    call(
        workbench,
        "POST",
        f"/api/v1/admin/evidence-requests/{request_id}/confirm-payment",
        workbench.admin_token,
        json={"amount": 100},
    )
    call(
        workbench,
        "POST",
        f"/api/v1/admin/evidence-requests/{request_id}/deliver",
        workbench.admin_token,
        json={"method": "当面", "note": "当面交付"},
    )
    call(
        workbench,
        "POST",
        f"/api/v1/admin/evidence-requests/{request_id}/notes",
        workbench.support_token,
        json={"text": "已和用户确认交付时间。"},
        expected=201,
    )
    call(
        workbench,
        "POST",
        f"/api/v1/admin/users/{user.id}/membership",
        workbench.admin_token,
        json={"tier": "vip", "days": 7, "reason": "演示开通"},
        expected=201,
    )
    call(
        workbench,
        "POST",
        f"/api/v1/admin/users/{user.id}/ban",
        workbench.admin_token,
        json={"type": "permanent", "reason": "演示封禁"},
    )
    call(
        workbench,
        "POST",
        f"/api/v1/admin/users/{user.id}/unban",
        workbench.admin_token,
    )
    order = add_order(workbench.session, user, workbench.clock.now, "DD-AUDIT-1")
    call(
        workbench,
        "POST",
        f"/api/v1/admin/orders/{order.id}/reissue",
        workbench.support_token,
        json={},
    )
    workbench.session.expire_all()
    actions = set(workbench.session.scalars(select(AuditLog.action)).all())
    for name in (
        "create_evidence_request",
        "accept_evidence",
        "set_quote",
        "confirm_payment",
        "record_delivery",
        "add_evidence_note",
        "grant_membership",
        "ban_user",
        "unban_user",
        "reissue_entitlement",
    ):
        assert name in actions
    logs = call(
        workbench, "GET", "/api/v1/admin/audit-logs?limit=200", workbench.admin_token
    ).json()
    uuid = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
    for row in logs:
        assert row["object_label"] not in {"", "记录"}
        assert uuid.fullmatch(row["object_label"]) is None
    ban = next(row for row in logs if row["action"] == "ban_user")
    assert ban["after_text"] == "状态 已封禁；期限 永久；原因 演示封禁"
    unban = next(row for row in logs if row["action"] == "unban_user")
    assert unban["after_text"] == "状态 已解禁"
    assert "已封禁" in unban["before_text"]


def test_refund_revokes_the_issued_entitlement_and_is_audited(workbench):
    user = add_user(workbench.session, workbench.clock.now, "90000008002")
    order = add_order(workbench.session, user, workbench.clock.now, "DD-AUDIT-2")
    call(
        workbench,
        "POST",
        f"/api/v1/admin/orders/{order.id}/reissue",
        workbench.admin_token,
        json={},
    )
    refunded = call(
        workbench,
        "POST",
        f"/api/v1/admin/orders/{order.id}/refund",
        workbench.admin_token,
        json={"reason": "演示退款"},
    ).json()
    assert refunded["status"] == "已退款"
    call(
        workbench,
        "POST",
        f"/api/v1/admin/orders/{order.id}/refund",
        workbench.support_token,
        json={"reason": "客服不能退款"},
        expected=403,
    )
    workbench.session.expire_all()
    grant = workbench.session.scalar(select(Entitlement).where(Entitlement.user_id == user.id))
    assert grant.expires_at <= workbench.clock.now
    actions = set(workbench.session.scalars(select(AuditLog.action)).all())
    assert "refund_order" in actions
    broken = add_order(workbench.session, user, workbench.clock.now, "DD-AUDIT-BROKEN")
    call(
        workbench,
        "POST",
        f"/api/v1/admin/orders/{broken.id}/refund",
        workbench.admin_token,
        json={"reason": "发放异常不能退款"},
        expected=409,
    )
    assert "reissue_entitlement" in actions


def test_invite_codes_follow_the_alphabet_and_support_cannot_manage_them(workbench):
    user = add_user(workbench.session, workbench.clock.now, "90000008003")
    created = call(
        workbench,
        "POST",
        "/api/v1/admin/invite-codes",
        workbench.admin_token,
        json={
            "count": 5,
            "channel": "示例渠道",
            "expires_at": (workbench.clock.now + timedelta(days=30)).isoformat(),
        },
        expected=201,
    ).json()
    assert len(created) == 5
    assert all(InviteAlphabet.valid(row["code"]) for row in created)
    assert all(row["creator"] == "wb-admin" for row in created)
    call(
        workbench,
        "POST",
        "/api/v1/admin/invite-codes",
        workbench.support_token,
        json={
            "count": 1,
            "channel": "示例渠道",
            "expires_at": (workbench.clock.now + timedelta(days=30)).isoformat(),
        },
        expected=403,
    )
    voided = call(
        workbench,
        "POST",
        f"/api/v1/admin/invite-codes/{created[0]['id']}/void",
        workbench.admin_token,
    ).json()
    assert voided["status"] == "已作废"
    call(
        workbench,
        "POST",
        f"/api/v1/admin/users/{user.id}/membership",
        workbench.admin_token,
        json={"tier": "regular", "days": 30, "reason": "不计入转化"},
        expected=201,
    )
    stats = call(workbench, "GET", "/api/v1/admin/invite-codes/stats", workbench.admin_token).json()
    channel = next(row for row in stats if row["channel"] == "示例渠道")
    assert channel["voided"] == 1
    assert channel["paid_within_30_days"] == 0
    workbench.session.expire_all()
    assert "void_invite" in set(workbench.session.scalars(select(AuditLog.action)).all())


def test_invite_clock_stays_beside_vip(workbench):
    user = add_user(workbench.session, workbench.clock.now, "90000008009", vip=True)
    vip = workbench.session.scalar(
        select(Entitlement).where(Entitlement.user_id == user.id, Entitlement.kind == "vip")
    )
    vip_expires = vip.expires_at
    created = call(
        workbench,
        "POST",
        "/api/v1/admin/invite-codes",
        workbench.admin_token,
        json={
            "count": 2,
            "channel": "同时计时",
            "expires_at": (workbench.clock.now + timedelta(days=10)).isoformat(),
        },
        expected=201,
    ).json()
    redeemed = workbench.client.post(
        "/api/v1/invite-codes/redeem",
        headers=workbench.client.auth(user.id),
        json={"code": created[0]["code"]},
    )
    assert redeemed.status_code == 200, redeemed.text
    assert redeemed.json()["source"] == "invite"
    workbench.session.expire_all()
    vip = workbench.session.scalar(
        select(Entitlement).where(Entitlement.user_id == user.id, Entitlement.kind == "vip")
    )
    invite = workbench.session.scalar(
        select(Entitlement).where(
            Entitlement.user_id == user.id,
            Entitlement.source == "invite",
        )
    )
    assert vip.expires_at == vip_expires
    assert invite.expires_at == workbench.clock.now + timedelta(days=30)
    again = workbench.client.post(
        "/api/v1/invite-codes/redeem",
        headers=workbench.client.auth(user.id),
        json={"code": created[1]["code"]},
    )
    assert again.status_code == 409
    assert again.json()["detail"] == "bound"


def test_content_is_admin_only_and_examples_stop_at_five(workbench):
    saved = call(
        workbench,
        "PUT",
        "/api/v1/admin/content/splash",
        workbench.admin_token,
        json={
            "enabled": True,
            "image_name": "splash_demo.png",
            "slogan": "片单检索",
            "jump_label": None,
        },
    ).json()
    assert saved["dwell_seconds"] == 5
    assert saved["enabled"] is True
    call(
        workbench,
        "PUT",
        "/api/v1/admin/content/splash",
        workbench.admin_token,
        json={
            "enabled": True,
            "image_name": "splash_demo.png",
            "slogan": "全平台永久免费",
            "jump_label": None,
        },
        expected=400,
    )
    call(
        workbench,
        "GET",
        "/api/v1/admin/content/splash",
        workbench.support_token,
        expected=403,
    )
    for index in range(5):
        call(
            workbench,
            "POST",
            "/api/v1/admin/content/examples",
            workbench.admin_token,
            json={"program_name": f"示例节目{index}"},
            expected=201,
        )
    call(
        workbench,
        "POST",
        "/api/v1/admin/content/examples",
        workbench.admin_token,
        json={"program_name": "再多一条"},
        expected=409,
    )
    workbench.session.expire_all()
    actions = set(workbench.session.scalars(select(AuditLog.action)).all())
    assert "update_splash" in actions
    assert "add_example" in actions


def test_index_import_is_audited(workbench):
    csv_text = (
        "task_id,状态,提交时间,filename,公网/专网,平台名\n"
        "imp00001,done,2026-11-01 11:12:24,公网-示例视频-电影-2026.11.01.mp4,公网,示例视频\n"
    )
    status = call(
        workbench,
        "POST",
        "/api/v1/admin/index/import",
        workbench.admin_token,
        json={"csv": csv_text},
    ).json()
    assert status["last_successful_index_at"] is not None
    assert status["runs"][0]["status"] == "success"
    assert status["runs"][0]["new_rows"] == 1
    call(
        workbench,
        "POST",
        "/api/v1/admin/index/import",
        workbench.support_token,
        json={"csv": csv_text},
        expected=403,
    )
    workbench.session.expire_all()
    logged = workbench.session.scalars(
        select(AuditLog).where(AuditLog.action == "import_index")
    ).all()
    assert logged
    assert logged[-1].after_json["status"] == "success"
