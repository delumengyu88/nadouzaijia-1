from datetime import datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DatabaseError

from nadou.admin.evidence import EvidenceLookup
from nadou.admin.working_days import WorkingDays
from nadou.models import AppendOnlyViolation, AuditLog, EvidenceNote, EvidenceRequest
from nadou.text import RecordAge
from tests.workbench_helpers import add_user, call


def test_working_day_skips_weekends():
    friday = datetime(2026, 11, 20, 15, 0, tzinfo=RecordAge.TZ)
    monday = datetime(2026, 11, 23, 15, 0, tzinfo=RecordAge.TZ)
    assert WorkingDays.add(friday, 1) == monday
    saturday = datetime(2026, 11, 21, 12, 0, tzinfo=RecordAge.TZ)
    assert WorkingDays.add(saturday, 1) == datetime(2026, 11, 23, 12, 0, tzinfo=RecordAge.TZ)


@pytest.mark.parametrize("who", ["admin", "support"])
def test_evidence_flow_for_each_role(workbench, who):
    token = workbench.admin_token if who == "admin" else workbench.support_token
    user = add_user(
        workbench.session, workbench.clock.now, f"9000000200{1 if who == 'admin' else 2}"
    )
    created = call(
        workbench,
        "POST",
        "/api/v1/admin/evidence-requests",
        token,
        json={
            "user_id": user.id,
            "request_type": "单条",
            "application_number": f"QZ-FLOW-{who}",
        },
        expected=201,
    ).json()
    request_id = created["id"]
    assert created["status"] == "已提交"
    assert created["application_number"] == f"QZ-FLOW-{who}"
    if who == "support":
        assert created["phone"] == "*******2002"
    else:
        assert created["phone"] == "90000002001"
    assert any(message["title"] == "新取证申请" for message in workbench.notifier.messages)
    listed = call(workbench, "GET", "/api/v1/admin/evidence-requests", token).json()
    assert any(row["id"] == request_id for row in listed)
    found = call(
        workbench,
        "GET",
        f"/api/v1/admin/evidence-requests/by-number/QZ-FLOW-{who}",
        token,
    ).json()
    assert found["id"] == request_id
    workbench.session.expire_all()
    assert (
        EvidenceLookup.by_application_number(workbench.session, f"QZ-FLOW-{who}").id == request_id
    )

    call(workbench, "POST", f"/api/v1/admin/evidence-requests/{request_id}/accept", token)
    call(
        workbench,
        "POST",
        f"/api/v1/admin/evidence-requests/{request_id}/quote",
        token,
        json={"amount": 100},
    )
    call(
        workbench,
        "POST",
        f"/api/v1/admin/evidence-requests/{request_id}/confirm-payment",
        token,
        json={"amount": 100},
    )
    delivered = call(
        workbench,
        "POST",
        f"/api/v1/admin/evidence-requests/{request_id}/deliver",
        token,
        json={"method": "当面"},
    ).json()
    assert delivered["status"] == "已交付"
    assert delivered["delivery_method"] == "当面"
    blob = str(delivered)
    assert "http://" not in blob
    assert "https://" not in blob
    assert ".zip" not in blob

    unpaid = call(
        workbench,
        "POST",
        "/api/v1/admin/evidence-requests",
        token,
        json={"user_id": user.id, "request_type": "批量", "application_number": f"QZ-UNPAID-{who}"},
        expected=201,
    ).json()
    call(workbench, "POST", f"/api/v1/admin/evidence-requests/{unpaid['id']}/accept", token)
    failed = call(
        workbench,
        "POST",
        f"/api/v1/admin/evidence-requests/{unpaid['id']}/fail",
        token,
        json={"reason": "用户取消"},
    ).json()
    assert failed["status"] == "取证失败"
    assert failed["refund_method"] is None
    assert failed["refund_amount"] is None
    workbench.session.expire_all()
    actions = set(workbench.session.scalars(select(AuditLog.action)).all())
    assert {
        "create_evidence_request",
        "accept_evidence",
        "set_quote",
        "confirm_payment",
        "record_delivery",
        "fail_evidence",
    } <= actions


def test_refund_rules_and_delivery_methods(workbench):
    token = workbench.admin_token
    support = workbench.support_token
    user = add_user(workbench.session, workbench.clock.now, "90000002003")

    def make(number: str) -> str:
        created = call(
            workbench,
            "POST",
            "/api/v1/admin/evidence-requests",
            token,
            json={"user_id": user.id, "request_type": "单条", "application_number": number},
            expected=201,
        ).json()
        return created["id"]

    pending = make("QZ-RULE-PENDING")
    call(
        workbench,
        "POST",
        f"/api/v1/admin/evidence-requests/{pending}/fail",
        token,
        json={"reason": "太早"},
        expected=409,
    )

    unpaid = make("QZ-RULE-UNPAID")
    call(workbench, "POST", f"/api/v1/admin/evidence-requests/{unpaid}/accept", token)
    call(
        workbench,
        "POST",
        f"/api/v1/admin/evidence-requests/{unpaid}/fail",
        support,
        json={"reason": "不退", "refund_method": "现金"},
        expected=400,
    )
    call(
        workbench,
        "POST",
        f"/api/v1/admin/evidence-requests/{unpaid}/fail",
        token,
        json={"reason": "不退", "refund_amount": 1},
        expected=400,
    )

    paid = make("QZ-RULE-PAID")
    call(workbench, "POST", f"/api/v1/admin/evidence-requests/{paid}/accept", token)
    call(
        workbench,
        "POST",
        f"/api/v1/admin/evidence-requests/{paid}/quote",
        token,
        json={"amount": 80},
    )
    call(
        workbench,
        "POST",
        f"/api/v1/admin/evidence-requests/{paid}/confirm-payment",
        token,
        json={"amount": 80},
    )
    call(
        workbench,
        "POST",
        f"/api/v1/admin/evidence-requests/{paid}/fail",
        token,
        json={"reason": "无法取证"},
        expected=400,
    )
    defaulted = call(
        workbench,
        "POST",
        f"/api/v1/admin/evidence-requests/{paid}/fail",
        token,
        json={"reason": "无法取证", "refund_method": "原路退回"},
    ).json()
    assert defaulted["refund_amount"] == 80
    assert defaulted["refund_method"] == "原路退回"

    custom = make("QZ-RULE-CUSTOM")
    call(workbench, "POST", f"/api/v1/admin/evidence-requests/{custom}/accept", token)
    call(
        workbench,
        "POST",
        f"/api/v1/admin/evidence-requests/{custom}/quote",
        token,
        json={"amount": 80},
    )
    call(
        workbench,
        "POST",
        f"/api/v1/admin/evidence-requests/{custom}/confirm-payment",
        token,
        json={"amount": 80},
    )
    adjusted = call(
        workbench,
        "POST",
        f"/api/v1/admin/evidence-requests/{custom}/fail",
        token,
        json={"reason": "部分退回", "refund_method": "原路退回", "refund_amount": 30},
    ).json()
    assert adjusted["refund_amount"] == 30

    delivery = make("QZ-RULE-DELIVERY")
    call(workbench, "POST", f"/api/v1/admin/evidence-requests/{delivery}/accept", token)
    call(
        workbench,
        "POST",
        f"/api/v1/admin/evidence-requests/{delivery}/quote",
        token,
        json={"amount": 10},
    )
    call(
        workbench,
        "POST",
        f"/api/v1/admin/evidence-requests/{delivery}/confirm-payment",
        token,
        json={"amount": 10},
    )
    call(
        workbench,
        "POST",
        f"/api/v1/admin/evidence-requests/{delivery}/deliver",
        token,
        json={"method": "邮寄"},
        expected=400,
    )
    call(
        workbench,
        "POST",
        f"/api/v1/admin/evidence-requests/{delivery}/deliver",
        token,
        json={"method": "其他"},
        expected=400,
    )
    for method in ("快递", "企业网盘"):
        another = make(f"QZ-RULE-{method}")
        call(workbench, "POST", f"/api/v1/admin/evidence-requests/{another}/accept", token)
        call(
            workbench,
            "POST",
            f"/api/v1/admin/evidence-requests/{another}/quote",
            token,
            json={"amount": 10},
        )
        call(
            workbench,
            "POST",
            f"/api/v1/admin/evidence-requests/{another}/confirm-payment",
            token,
            json={"amount": 10},
        )
        body = call(
            workbench,
            "POST",
            f"/api/v1/admin/evidence-requests/{another}/deliver",
            support,
            json={"method": method},
        ).json()
        assert body["delivery_method"] == method
    noted = call(
        workbench,
        "POST",
        f"/api/v1/admin/evidence-requests/{delivery}/deliver",
        support,
        json={"method": "其他", "note": "柜台交接"},
    ).json()
    assert noted["delivery_note"] == "柜台交接"
    assert "http" not in str(noted)


def test_notes_are_append_only_and_returned_on_detail(workbench):
    user = add_user(workbench.session, workbench.clock.now, "90000002004")
    created = call(
        workbench,
        "POST",
        "/api/v1/admin/evidence-requests",
        workbench.support_token,
        json={"user_id": user.id, "request_type": "单条", "application_number": "QZ-NOTE-1"},
        expected=201,
    ).json()
    first = call(
        workbench,
        "POST",
        f"/api/v1/admin/evidence-requests/{created['id']}/notes",
        workbench.support_token,
        json={"text": "用户来电说明了节目名"},
        expected=201,
    ).json()
    second = call(
        workbench,
        "POST",
        f"/api/v1/admin/evidence-requests/{created['id']}/notes",
        workbench.admin_token,
        json={"text": "已约好下周一当面"},
        expected=201,
    ).json()
    assert first["author"] == "wb-support"
    assert second["author"] == "wb-admin"
    detail = call(
        workbench,
        "GET",
        "/api/v1/admin/evidence-requests/by-number/QZ-NOTE-1",
        workbench.support_token,
    ).json()
    assert detail["phone"] == "*******2004"
    workbench.session.expire_all()
    noted = set(workbench.session.scalars(select(AuditLog.action)).all())
    assert "add_evidence_note" in noted
    assert [note["text"] for note in detail["notes"]] == [
        "用户来电说明了节目名",
        "已约好下周一当面",
    ]
    assert detail["notes"][0]["created_at"]
    missing = workbench.client.delete(
        f"/api/v1/admin/evidence-requests/{created['id']}/notes",
        headers={"Authorization": f"Bearer {workbench.admin_token}"},
    )
    assert missing.status_code == 405
    workbench.session.expire_all()
    note = workbench.session.get(EvidenceNote, first["id"])
    note.body = "改掉"
    with pytest.raises(AppendOnlyViolation):
        workbench.session.flush()
    workbench.session.rollback()
    with pytest.raises(DatabaseError):
        workbench.session.execute(text("UPDATE evidence_notes SET body = '改掉'"))
    workbench.session.rollback()


def test_overdue_is_sorted_first_and_notified_once(workbench):
    vip = add_user(workbench.session, workbench.clock.now, "90000002005", vip=True)
    plain = add_user(workbench.session, workbench.clock.now, "90000002006")
    old = call(
        workbench,
        "POST",
        "/api/v1/admin/evidence-requests",
        workbench.admin_token,
        json={"user_id": plain.id, "request_type": "单条", "application_number": "QZ-OLD"},
        expected=201,
    ).json()
    fresh_vip = call(
        workbench,
        "POST",
        "/api/v1/admin/evidence-requests",
        workbench.admin_token,
        json={"user_id": vip.id, "request_type": "单条", "application_number": "QZ-VIP"},
        expected=201,
    ).json()
    fresh_plain = call(
        workbench,
        "POST",
        "/api/v1/admin/evidence-requests",
        workbench.admin_token,
        json={"user_id": plain.id, "request_type": "批量", "application_number": "QZ-NEW"},
        expected=201,
    ).json()
    workbench.session.expire_all()
    row = workbench.session.get(EvidenceRequest, old["id"])
    row.created_at = workbench.clock.now - timedelta(days=4)
    workbench.session.commit()
    before = len([item for item in workbench.notifier.messages if item["title"] == "取证申请逾期"])
    listed = call(
        workbench,
        "GET",
        "/api/v1/admin/evidence-requests?type=单条",
        workbench.admin_token,
    ).json()
    assert [item["application_number"] for item in listed[:2]] == ["QZ-OLD", "QZ-VIP"]
    assert listed[0]["overdue"] is True
    assert listed[1]["vip"] is True
    assert fresh_plain["id"] not in {item["id"] for item in listed}
    assert fresh_vip["id"] in {item["id"] for item in listed}
    overdue_messages = [
        item for item in workbench.notifier.messages if item["title"] == "取证申请逾期"
    ]
    assert len(overdue_messages) == before + 1
    assert "QZ-OLD" in overdue_messages[-1]["body"]
    assert "http" not in overdue_messages[-1]["body"]
    call(workbench, "GET", "/api/v1/admin/evidence-requests", workbench.admin_token)
    assert (
        len([item for item in workbench.notifier.messages if item["title"] == "取证申请逾期"])
        == before + 1
    )
    workbench.session.expire_all()
    actions = set(workbench.session.scalars(select(AuditLog.action)).all())
    assert "create_evidence_request" in actions
