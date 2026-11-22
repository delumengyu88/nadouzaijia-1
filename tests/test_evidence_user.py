import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import sessionmaker

from nadou.admin.evidence import ApplicationNumbers
from nadou.admin.notify import Notifier
from nadou.auth import UserSessions
from nadou.config import get_settings
from nadou.db import Base, Database, create_engine_for
from nadou.evidence_user import EvidenceUserBook, EvidenceUserStatus
from nadou.main import create_app
from nadou.models import AuditLog, EvidenceRequest, IndexRecord, User
from tests.workbench_helpers import add_user, call

BEIJING = ZoneInfo("Asia/Shanghai")
PHONE = "13812348000"
MASKED = "138****8000"
OPEN_STATUSES = ("已提交", "已受理", "待付款（线下）", "取证中")
CLOSED_STATUSES = ("已交付", "已完成", "取证失败", "已关闭", "已取消")


class RaisingNotifier(Notifier):
    def notify(self, title: str, body: str) -> None:
        raise RuntimeError("down")


def login(workbench, phone: str | None = PHONE):
    if phone is None:
        user = User(id=str(uuid4()), created_at=workbench.clock.now)
        workbench.session.add(user)
        workbench.session.commit()
    else:
        user = add_user(workbench.session, workbench.clock.now, phone)
    return user, workbench.client.auth(user.id)


def submit(workbench, headers, **fields):
    return workbench.client.post("/api/v1/evidence-requests", headers=headers, json=fields)


def fresh(workbench, request_id: str) -> EvidenceRequest:
    workbench.session.rollback()
    workbench.session.expire_all()
    row = workbench.session.get(EvidenceRequest, request_id)
    assert row is not None
    return row


def test_guest_is_unauthorized_on_every_route(workbench):
    routes = (
        ("POST", "/api/v1/evidence-requests"),
        ("GET", "/api/v1/evidence-requests"),
        ("GET", "/api/v1/evidence-requests/missing"),
        ("POST", "/api/v1/evidence-requests/missing/withdraw"),
        ("POST", "/api/v1/evidence-requests/missing/confirm"),
    )
    me = workbench.client.get("/api/v1/auth/me")
    assert me.status_code == 401
    for method, path in routes:
        response = workbench.client.request(method, path, json={"name": "节目"})
        assert response.status_code == 401, path
        assert response.json()["detail"] == me.json()["detail"]


def test_submit_stores_normalized_fields_and_workbench_number(workbench):
    _user, headers = login(workbench)
    response = submit(
        workbench,
        headers,
        name="  海边来信  ",
        kind="normal",
        platform=" 广东移动 ",
        note=" 请尽快 ",
        phone=f" {PHONE} ",
        record_ref="should-not-stick",
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == {"id", "no"}
    assert body["no"].startswith("QZ20261121")
    assert PHONE not in response.text
    row = fresh(workbench, body["id"])
    assert row.application_number == body["no"]
    assert row.program_name == "海边来信"
    assert row.platform_label == "广东移动"
    assert row.requirement == "请尽快"
    assert row.contact_phone == PHONE
    assert row.request_type == "常规"
    assert row.status == "已提交"
    assert row.record_ref is None
    assert row.user_id == _user.id
    for stamp in (
        row.accepted_at,
        row.quoted_at,
        row.payment_confirmed_at,
        row.delivered_at,
        row.completed_at,
        row.failed_at,
        row.cancelled_at,
        row.closed_at,
    ):
        assert stamp is None


def test_recent_request_drops_platform(workbench):
    _user, headers = login(workbench)
    response = submit(
        workbench,
        headers,
        name="近期节目",
        kind="recent",
        platform="广东移动",
        phone=PHONE,
    )
    assert response.status_code == 201, response.text
    row = fresh(workbench, response.json()["id"])
    assert row.request_type == "近期"
    assert row.platform_label is None
    detail = workbench.client.get(
        f"/api/v1/evidence-requests/{row.id}",
        headers=headers,
    )
    assert detail.json()["kind"] == "recent"
    assert detail.json()["platform"] is None
    assert "广东移动" not in detail.text


def test_phone_is_required_and_not_taken_from_the_account(workbench):
    user, headers = login(workbench, "13900001111")
    required = {"code": "phone_required", "message": "请填写联系手机号"}
    missing = submit(workbench, headers, name="无号节目", kind="normal")
    blank = submit(workbench, headers, name="无号节目", kind="normal", phone="   ")
    empty = submit(workbench, headers, name="无号节目", kind="normal", phone=None)
    for response in (missing, blank, empty):
        assert response.status_code == 422
        assert response.json() == required
        assert "13900001111" not in response.text
    wrong = submit(workbench, headers, name="无号节目", kind="normal", phone="23812348000")
    assert wrong.status_code == 422
    assert wrong.json() == {"code": "invalid_phone", "message": "请填写正确的手机号"}
    assert "23812348000" not in wrong.text
    assert "13900001111" not in wrong.text
    assert workbench.session.query(EvidenceRequest).count() == 0
    created = submit(workbench, headers, name="自填节目", kind="normal", phone="13700008888")
    assert created.status_code == 201, created.text
    assert fresh(workbench, created.json()["id"]).contact_phone == "13700008888"
    assert user.phone == "13900001111"


def test_name_kind_and_note_validation(workbench):
    _user, headers = login(workbench)
    blank = submit(workbench, headers, name="   ", kind="normal", phone=PHONE)
    assert blank.status_code == 422
    assert blank.json()["code"] == "name_required"
    long_name = submit(workbench, headers, name="节" * 201, kind="normal", phone=PHONE)
    assert long_name.status_code == 422
    assert long_name.json()["code"] == "name_too_long"
    kind = submit(workbench, headers, name="类型节目", kind="batch", phone=PHONE)
    assert kind.status_code == 422
    assert kind.json()["code"] == "invalid_kind"
    note = submit(
        workbench,
        headers,
        name="备注节目",
        kind="normal",
        phone=PHONE,
        note="字" * 501,
    )
    assert note.status_code == 422
    assert note.json()["code"] == "note_too_long"
    assert all(item.status_code != 409 for item in (blank, long_name, kind, note))


def test_duplicate_uses_normalized_name_and_returns_the_existing_ticket(workbench):
    _user, headers = login(workbench)
    first = submit(workbench, headers, name="　节目Ａ　", kind="normal", phone=PHONE)
    assert first.status_code == 201, first.text
    again = submit(workbench, headers, name=" 节目A ", kind="recent", phone=PHONE)
    assert again.status_code == 409, again.text
    assert again.json() == {
        "code": "duplicate",
        "id": first.json()["id"],
        "no": first.json()["no"],
        "message": "这个节目已经提交过取证申请",
    }
    stored = fresh(workbench, first.json()["id"])
    assert stored.program_name == "节目A"
    assert workbench.session.query(EvidenceRequest).count() == 1


@pytest.mark.parametrize("status", OPEN_STATUSES)
def test_open_status_blocks_the_same_name(workbench, status):
    _user, headers = login(workbench, f"1380000{OPEN_STATUSES.index(status):04d}")
    first = submit(workbench, headers, name="未结束节目", kind="normal", phone=PHONE)
    assert first.status_code == 201, first.text
    row = fresh(workbench, first.json()["id"])
    row.status = status
    workbench.session.commit()
    again = submit(workbench, headers, name="未结束节目", kind="normal", phone=PHONE)
    assert again.status_code == 409
    assert again.json()["code"] == "duplicate"
    assert again.json()["id"] == first.json()["id"]
    assert again.json()["no"] == first.json()["no"]


@pytest.mark.parametrize("status", CLOSED_STATUSES)
def test_finished_status_allows_the_same_name(workbench, status):
    _user, headers = login(workbench, f"1370000{CLOSED_STATUSES.index(status):04d}")
    first = submit(workbench, headers, name="可再提交", kind="normal", phone=PHONE)
    row = fresh(workbench, first.json()["id"])
    row.status = status
    workbench.session.commit()
    again = submit(workbench, headers, name="可再提交", kind="normal", phone=PHONE)
    assert again.status_code == 201, again.text
    assert again.json()["id"] != first.json()["id"]


def test_another_user_can_submit_the_same_name(workbench):
    _first, first_headers = login(workbench, "13800001001")
    _second, second_headers = login(workbench, "13800001002")
    one = submit(workbench, first_headers, name="同名节目", kind="normal", phone="13800001001")
    two = submit(workbench, second_headers, name="同名节目", kind="normal", phone="13800001002")
    assert one.status_code == 201
    assert two.status_code == 201
    assert one.json()["id"] != two.json()["id"]


def test_list_is_only_mine_and_newest_first(workbench):
    owner, headers = login(workbench, "13800002001")
    other, other_headers = login(workbench, "13800002002")
    older = submit(workbench, headers, name="较早节目", kind="normal", phone="13800002001")
    assert older.status_code == 201, older.text
    workbench.clock.now = workbench.clock.now + timedelta(hours=2)
    newer = submit(workbench, headers, name="较新节目", kind="recent", phone="13800002001")
    assert newer.status_code == 201, newer.text
    foreign = submit(
        workbench,
        other_headers,
        name="别人的节目",
        kind="normal",
        phone="13800002002",
    )
    assert foreign.status_code == 201, foreign.text
    listed = workbench.client.get("/api/v1/evidence-requests", headers=headers)
    assert listed.status_code == 200
    body = listed.json()
    assert [item["name"] for item in body] == ["较新节目", "较早节目"]
    assert [item["no"] for item in body] == [newer.json()["no"], older.json()["no"]]
    assert body[0]["kind"] == "recent"
    assert body[0]["status"] == "submitted"
    assert body[0]["status_text"] == "已提交"
    assert set(body[0]) == {
        "id",
        "no",
        "name",
        "platform",
        "kind",
        "status",
        "status_text",
        "created_at",
    }
    assert "13800002001" not in listed.text
    assert "别人的节目" not in listed.text
    theirs = workbench.client.get("/api/v1/evidence-requests", headers=other_headers)
    assert [item["name"] for item in theirs.json()] == ["别人的节目"]
    assert owner.id != other.id


def test_detail_hides_other_people_and_masks_the_phone(workbench):
    _owner, headers = login(workbench, PHONE)
    _other, other_headers = login(workbench, "13900002222")
    created = submit(
        workbench,
        headers,
        name="详情节目",
        kind="normal",
        platform="浙江移动",
        note="带备注",
        phone=PHONE,
    )
    request_id = created.json()["id"]
    detail = workbench.client.get(f"/api/v1/evidence-requests/{request_id}", headers=headers)
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["note"] == "带备注"
    assert body["phone"] == MASKED
    assert body["platform"] == "浙江移动"
    assert body["quote_amount"] is None
    assert body["failure_reason"] is None
    assert body["refund_method"] is None
    assert body["refund_amount"] is None
    assert body["refund_note"] is None
    assert body["cancelled_at"] is None
    assert PHONE not in detail.text
    missing = workbench.client.get(
        f"/api/v1/evidence-requests/{request_id}",
        headers=other_headers,
    )
    absent = workbench.client.get("/api/v1/evidence-requests/missing", headers=headers)
    assert missing.status_code == 404
    assert absent.status_code == 404
    assert missing.json() == absent.json() == {"code": "not_found", "message": "申请不存在"}
    assert PHONE not in missing.text


def test_status_codes_follow_the_stored_chinese(workbench):
    _user, headers = login(workbench)
    created = submit(workbench, headers, name="状态节目", kind="normal", phone=PHONE)
    request_id = created.json()["id"]
    for stored, code in EvidenceUserStatus.CODES.items():
        row = fresh(workbench, request_id)
        row.status = stored
        workbench.session.commit()
        detail = workbench.client.get(f"/api/v1/evidence-requests/{request_id}", headers=headers)
        assert detail.status_code == 200
        assert detail.json()["status"] == code
        assert detail.json()["status_text"] == stored


def test_withdraw_and_confirm_follow_status(workbench):
    user, headers = login(workbench)
    created = submit(workbench, headers, name="撤回节目", kind="normal", phone=PHONE)
    request_id = created.json()["id"]
    withdrawn = workbench.client.post(
        f"/api/v1/evidence-requests/{request_id}/withdraw",
        headers=headers,
    )
    assert withdrawn.status_code == 200, withdrawn.text
    assert withdrawn.json()["status"] == "cancelled"
    assert withdrawn.json()["status_text"] == "已取消"
    assert withdrawn.json()["cancelled_at"]
    assert withdrawn.json()["phone"] == MASKED
    assert PHONE not in withdrawn.text
    row = fresh(workbench, request_id)
    assert row.status == "已取消"
    assert row.cancelled_at is not None
    again = submit(workbench, headers, name="撤回节目", kind="normal", phone=PHONE)
    assert again.status_code == 201, again.text

    blocked = submit(workbench, headers, name="不能撤回", kind="normal", phone=PHONE)
    blocked_id = blocked.json()["id"]
    refused_withdraw = (
        "已受理",
        "待付款（线下）",
        "取证中",
        "已交付",
        "已完成",
        "取证失败",
        "已关闭",
        "已取消",
    )
    for status in refused_withdraw:
        row = fresh(workbench, blocked_id)
        row.status = status
        workbench.session.commit()
        refused = workbench.client.post(
            f"/api/v1/evidence-requests/{blocked_id}/withdraw",
            headers=headers,
        )
        assert refused.status_code == 409, status
        assert refused.json()["code"] == "invalid_state"
        assert refused.json()["message"] == "当前状态不能撤回"
        assert "invalid_status" not in refused.text
        assert fresh(workbench, blocked_id).status == status

    delivered = submit(workbench, headers, name="确认节目", kind="normal", phone=PHONE)
    delivered_id = delivered.json()["id"]
    row = fresh(workbench, delivered_id)
    row.status = "已交付"
    workbench.session.commit()
    confirmed = workbench.client.post(
        f"/api/v1/evidence-requests/{delivered_id}/confirm",
        headers=headers,
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "done"
    assert confirmed.json()["status_text"] == "已完成"
    assert confirmed.json()["completed_at"]
    assert PHONE not in confirmed.text
    row = fresh(workbench, delivered_id)
    assert row.status == "已完成"
    assert row.completed_at is not None

    not_delivered = submit(workbench, headers, name="不能确认", kind="normal", phone=PHONE)
    not_id = not_delivered.json()["id"]
    refused_confirm = (
        "已提交",
        "已受理",
        "待付款（线下）",
        "取证中",
        "已完成",
        "取证失败",
        "已关闭",
        "已取消",
    )
    for status in refused_confirm:
        row = fresh(workbench, not_id)
        row.status = status
        workbench.session.commit()
        refused = workbench.client.post(
            f"/api/v1/evidence-requests/{not_id}/confirm",
            headers=headers,
        )
        assert refused.status_code == 409, status
        assert refused.json()["code"] == "invalid_state"
        assert refused.json()["message"] == "当前状态不能确认"
        assert fresh(workbench, not_id).status == status

    workbench.session.rollback()
    audits = workbench.session.scalars(
        select(AuditLog).where(AuditLog.action.in_(("withdraw_evidence", "confirm_evidence")))
    ).all()
    assert {row.action for row in audits} == {"withdraw_evidence", "confirm_evidence"}
    for audit in audits:
        assert audit.actor_id is None
        assert audit.actor_username is None
        assert audit.after_json["user_id"] == user.id
        dumped = json.dumps(audit.before_json) + json.dumps(audit.after_json)
        assert PHONE not in dumped

    stranger = workbench.client.post(
        f"/api/v1/evidence-requests/{delivered_id}/withdraw",
        headers=workbench.client.auth(
            add_user(workbench.session, workbench.clock.now, "13600003333").id
        ),
    )
    assert stranger.status_code == 404
    assert stranger.json()["code"] == "not_found"


def test_notice_is_masked_and_a_failure_still_submits(workbench, caplog):
    _user, headers = login(workbench, PHONE)
    caplog.set_level(logging.DEBUG)
    before = len(workbench.notifier.messages)
    created = submit(
        workbench,
        headers,
        name="通知节目",
        kind="normal",
        phone=PHONE,
    )
    assert created.status_code == 201, created.text
    sent = workbench.notifier.messages[before:]
    assert len(sent) == 1
    assert sent[0]["title"] == "新的委托取证"
    assert "通知节目" in sent[0]["body"]
    assert created.json()["no"] in sent[0]["body"]
    assert "常规取证" in sent[0]["body"]
    assert MASKED in sent[0]["body"]
    assert PHONE not in sent[0]["title"]
    assert PHONE not in sent[0]["body"]
    assert PHONE not in caplog.text

    app = create_app(
        database=Database(engine=workbench.session.get_bind()),
        now=workbench.clock,
        notifier=RaisingNotifier(),
    )
    with TestClient(app) as client:
        failed_notice = client.post(
            "/api/v1/evidence-requests",
            headers=headers,
            json={"name": "通知失败节目", "kind": "recent", "phone": PHONE},
        )
    assert failed_notice.status_code == 201, failed_notice.text
    assert PHONE not in failed_notice.text
    row = fresh(workbench, failed_notice.json()["id"])
    assert row.program_name == "通知失败节目"
    assert row.request_type == "近期"
    assert PHONE not in caplog.text


def test_notice_runs_only_after_the_row_is_committed(workbench):
    seen: dict[str, str | None] = {"no": None}

    class AfterCommit(Notifier):
        def notify(self, title: str, body: str) -> None:
            other = sessionmaker(bind=workbench.session.get_bind(), expire_on_commit=False)()
            try:
                row = other.scalar(
                    select(EvidenceRequest).where(EvidenceRequest.program_name == "提交后通知")
                )
                seen["no"] = None if row is None else row.application_number
            finally:
                other.close()
            assert PHONE not in title and PHONE not in body

    app = create_app(
        database=Database(engine=workbench.session.get_bind()),
        now=workbench.clock,
        notifier=AfterCommit(),
    )
    _user, headers = login(workbench)
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/evidence-requests",
            headers=headers,
            json={"name": "提交后通知", "kind": "normal", "phone": PHONE},
        )
    assert created.status_code == 201, created.text
    assert seen["no"] == created.json()["no"]


def test_application_number_collision_is_503(workbench, monkeypatch):
    user, headers = login(workbench)
    taken = EvidenceRequest(
        id="taken-number",
        application_number="QZ-TAKEN",
        user_id=user.id,
        request_type="常规",
        status="已提交",
        created_at=workbench.clock.now,
        program_name="已占用编号",
        contact_phone=PHONE,
    )
    workbench.session.add(taken)
    workbench.session.commit()
    calls = {"n": 0}

    def collide(_session, _now):
        calls["n"] += 1
        return "QZ-TAKEN"

    monkeypatch.setattr(ApplicationNumbers, "next_value", collide)
    before = len(workbench.notifier.messages)
    response = submit(workbench, headers, name="发不出号", kind="normal", phone=PHONE)
    assert response.status_code == 503
    assert response.json() == {"code": "no_unavailable", "message": "提交失败，请稍后再试"}
    assert PHONE not in response.text
    assert calls["n"] == 3
    assert len(workbench.notifier.messages) == before
    workbench.session.rollback()
    workbench.session.expire_all()
    rows = workbench.session.scalars(select(EvidenceRequest)).all()
    assert [row.program_name for row in rows] == ["已占用编号"]


def test_generated_dates_are_not_copied_onto_the_request(workbench):
    generated = workbench.clock.now.replace(year=2024, month=1, day=2, hour=3, minute=4)
    workbench.session.add(
        IndexRecord(
            id="generated-record",
            task_id="generated-task",
            program_name="生成日期节目",
            program_name_raw="生成日期节目",
            hit_type="consensus",
            frame_count=1,
            timestamps=["00:00:01"],
            uploaded_at=None,
            generated_uploaded_at=generated,
            date_generated=True,
        )
    )
    workbench.session.commit()
    _user, headers = login(workbench)
    created = submit(
        workbench,
        headers,
        name="生成日期节目",
        kind="normal",
        phone=PHONE,
        record_ref="generated-record",
        uploaded_at="2024-01-02T03:04:00+08:00",
    )
    assert created.status_code == 201, created.text
    row = fresh(workbench, created.json()["id"])
    assert row.record_ref is None
    assert row.created_at != generated
    detail = workbench.client.get(
        f"/api/v1/evidence-requests/{row.id}",
        headers=headers,
    )
    assert "2024-01-02" not in detail.text
    assert "generated-record" not in detail.text


def test_admin_list_shows_the_member_request(workbench):
    _user, headers = login(workbench, "13800004444")
    created = submit(
        workbench,
        headers,
        name="工作台可见",
        kind="recent",
        platform="不该出现",
        phone="13900005555",
    )
    assert created.status_code == 201, created.text
    listed = call(
        workbench,
        "GET",
        "/api/v1/admin/evidence-requests",
        workbench.admin_token,
    ).json()
    found = [item for item in listed if item["application_number"] == created.json()["no"]]
    assert len(found) == 1
    assert found[0]["program_name"] == "工作台可见"
    assert found[0]["request_type"] == "近期"
    assert found[0]["status"] == "已提交"
    assert found[0]["platform_label"] is None
    admin_text = call(
        workbench,
        "GET",
        f"/api/v1/admin/evidence-requests/{created.json()['id']}",
        workbench.admin_token,
    ).text
    support_text = call(
        workbench,
        "GET",
        f"/api/v1/admin/evidence-requests/{created.json()['id']}",
        workbench.support_token,
    ).text
    assert "13900005555" not in admin_text
    assert "13900005555" not in support_text


def test_concurrent_submit_keeps_a_single_open_request(tmp_path, clock, monkeypatch):
    original = EvidenceUserBook._stage

    def slow(self, *args, **kwargs):
        time.sleep(1.0)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(EvidenceUserBook, "_stage", slow)
    engine = create_engine_for(f"sqlite:///{tmp_path}/race.db")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    setup = factory()
    user = User(id="race-user", created_at=clock, phone="13800006666")
    setup.add(user)
    setup.commit()
    token = UserSessions.issue(setup, user, clock)
    setup.commit()
    setup.close()
    notifier = workbench_notifier()
    app = create_app(database=Database(engine=engine), now=lambda: clock, notifier=notifier)
    headers = {"Authorization": f"Bearer {token}"}
    payload = {"name": "并发节目", "kind": "normal", "phone": "13800006666"}
    barrier = threading.Barrier(2)

    def once():
        barrier.wait(5)
        with TestClient(app) as client:
            return client.post("/api/v1/evidence-requests", headers=headers, json=payload)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = [job.result(timeout=15) for job in (pool.submit(once), pool.submit(once))]
    finally:
        engine.dispose()
    codes = sorted(response.status_code for response in responses)
    assert codes == [201, 409], [response.text for response in responses]
    created = next(response for response in responses if response.status_code == 201).json()
    duplicate = next(response for response in responses if response.status_code == 409).json()
    assert duplicate["code"] == "duplicate"
    assert duplicate["id"] == created["id"]
    assert duplicate["no"] == created["no"]
    assert duplicate["message"] == "这个节目已经提交过取证申请"
    assert "13800006666" not in duplicate["message"]
    check = factory()
    try:
        rows = check.scalars(select(EvidenceRequest)).all()
        assert len(rows) == 1
        assert rows[0].program_name == "并发节目"
    finally:
        check.close()
    assert len(notifier.messages) == 1
    assert "138****6666" in notifier.messages[0]["body"]
    assert "13800006666" not in notifier.messages[0]["body"]


def workbench_notifier():
    from nadou.admin.notify import FakeNotifier

    return FakeNotifier()


def test_contact_phone_migration_upgrades_and_downgrades(tmp_path, monkeypatch):
    """Exercise only 0012. Older revisions are not valid SQLite ALTER steps."""
    url = f"sqlite:///{tmp_path}/migrate.db"
    engine = create_engine_for(url)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE evidence_requests DROP COLUMN contact_phone"))
    engine.dispose()
    assert "contact_phone" not in _columns(url)
    _insert_existing_request(url)
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    try:
        command.stamp(config, "20261122_0011")
        command.upgrade(config, "head")
        assert "contact_phone" in _columns(url)
        kept = _read_request(url)
        assert kept["program_name"] == "保留节目"
        assert kept["contact_phone"] is None
        command.downgrade(config, "20261122_0011")
        assert "contact_phone" not in _columns(url)
        assert _program_name(url) == "保留节目"
        command.upgrade(config, "head")
        assert _read_request(url)["program_name"] == "保留节目"
        assert _read_request(url)["contact_phone"] is None
    finally:
        get_settings.cache_clear()


def _columns(url: str) -> set[str]:
    engine = create_engine_for(url)
    try:
        return {column["name"] for column in inspect(engine).get_columns("evidence_requests")}
    finally:
        engine.dispose()


def _insert_existing_request(url: str) -> None:
    engine = create_engine_for(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO users (id, created_at) VALUES ('user-keep', :created)"),
                {"created": "2026-11-21 04:00:00"},
            )
            connection.execute(
                text(
                    "INSERT INTO evidence_requests "
                    "(id, application_number, user_id, request_type, status, created_at, "
                    "program_name) "
                    "VALUES ('request-keep', 'QZ-KEEP', 'user-keep', '常规', '已提交', "
                    ":created, '保留节目')"
                ),
                {"created": "2026-11-21 04:00:00"},
            )
    finally:
        engine.dispose()


def _program_name(url: str) -> str:
    engine = create_engine_for(url)
    try:
        with engine.connect() as connection:
            return connection.execute(
                text("SELECT program_name FROM evidence_requests WHERE id = 'request-keep'")
            ).scalar_one()
    finally:
        engine.dispose()


def _read_request(url: str) -> dict:
    engine = create_engine_for(url)
    try:
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT program_name, contact_phone "
                    "FROM evidence_requests WHERE id = 'request-keep'"
                )
            ).one()
    finally:
        engine.dispose()
    return {"program_name": row[0], "contact_phone": row[1]}
