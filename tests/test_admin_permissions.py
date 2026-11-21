from tests.workbench_helpers import add_order, add_user, call


def test_anonymous_requests_are_rejected(workbench):
    from fastapi.testclient import TestClient

    user = add_user(workbench.session, workbench.clock.now, "90000001001")
    workbench.client = TestClient(workbench.app)
    paths = [
        ("GET", "/api/v1/admin/auth/me", None),
        ("GET", "/api/v1/admin/staff", None),
        ("GET", "/api/v1/admin/audit-logs", None),
        ("GET", "/api/v1/admin/evidence-requests", None),
        ("POST", "/api/v1/admin/evidence-requests", {"user_id": user.id, "request_type": "单条"}),
        ("GET", "/api/v1/admin/users?phone=90000001001", None),
        ("GET", "/api/v1/admin/orders", None),
        ("GET", "/api/v1/admin/orders/export", None),
        ("GET", "/api/v1/admin/index/status", None),
    ]
    for method, path, body in paths:
        call(workbench, method, path, None, json=body, expected=401)


def test_support_cannot_call_admin_only_endpoints(workbench):
    user = add_user(workbench.session, workbench.clock.now, "90000001002")
    order = add_order(
        workbench.session, user, workbench.clock.now, "DD-TEST-9001", status="已支付", issuance=None
    )
    support = workbench.support_token
    denied = [
        (
            "POST",
            "/api/v1/admin/staff",
            {"username": "nope-user", "password": "nope-pass-1", "role": "support"},
        ),
        ("GET", "/api/v1/admin/staff", None),
        ("POST", "/api/v1/admin/staff/missing/unlock", None),
        ("GET", "/api/v1/admin/audit-logs", None),
        ("POST", f"/api/v1/admin/users/{user.id}/ban", {"type": "permanent", "reason": "滥用"}),
        ("POST", f"/api/v1/admin/users/{user.id}/unban", None),
        (
            "POST",
            f"/api/v1/admin/users/{user.id}/membership",
            {"tier": "vip", "days": 7, "reason": "补偿"},
        ),
        ("GET", "/api/v1/admin/orders/export", None),
        ("GET", "/api/v1/admin/index/status", None),
        ("POST", f"/api/v1/admin/orders/{order.id}/reissue", {}),
    ]
    for method, path, body in denied[:-1]:
        call(workbench, method, path, support, json=body, expected=403)
    # Reissue of an order that is not 发放异常 is still allowed to be attempted by support;
    # this row is only here so the admin-only list stays free of it. Support may call reissue.
    call(workbench, "POST", denied[-1][1], support, json={}, expected=409)


def test_admin_can_manage_staff_and_read_index_status(workbench):
    created = call(
        workbench,
        "POST",
        "/api/v1/admin/staff",
        workbench.admin_token,
        json={"username": "wb-extra", "password": "extra-pass-1", "role": "support"},
        expected=201,
    )
    assert created.json()["role"] == "support"
    assert "password" not in created.json()
    listed = call(workbench, "GET", "/api/v1/admin/staff", workbench.admin_token).json()
    assert {row["username"] for row in listed} >= {"wb-admin", "wb-support", "wb-extra"}
    status = call(workbench, "GET", "/api/v1/admin/index/status", workbench.admin_token).json()
    assert status["last_successful_index_at"] is None
    assert status["stale"] is False
    assert status["runs"] == []
    assert status["unrecognized"] == []
    logs = call(workbench, "GET", "/api/v1/admin/audit-logs", workbench.admin_token).json()
    assert any(row["action"] == "create_staff" for row in logs)
    assert all("password" not in str(row) for row in logs)
