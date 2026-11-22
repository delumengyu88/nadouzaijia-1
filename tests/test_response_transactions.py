import json

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from nadou.admin.auth import StaffSessions
from nadou.models import EvidenceRequest
from tests.workbench_helpers import add_user, auth, call


class ObserveResponse:
    """Read through a separate connection when the client receives the response."""

    def __init__(self, app, observe):
        self.app = app
        self.observe = observe

    async def __call__(self, scope, receive, send):
        async def observed_send(message):
            if message["type"] == "http.response.body":
                self.observe(json.loads(message["body"]))
            await send(message)

        await self.app(scope, receive, observed_send)


def test_login_session_is_committed_before_token_reaches_client(workbench):
    visible = []

    def observe(body):
        with Session(workbench.session.get_bind()) as reader:
            account = StaffSessions.resolve(reader, body["token"], workbench.clock.now)
            visible.append(None if account is None else account.username)

    client = TestClient(ObserveResponse(workbench.app, observe))
    response = client.post(
        "/api/v1/admin/auth/login",
        json={"username": "wb-support", "password": "support-pass-1"},
    )
    assert response.status_code == 200
    assert visible == ["wb-support"]


def test_offline_payment_is_committed_before_success_reaches_client(workbench):
    user = add_user(workbench.session, workbench.clock.now, "90000008999")
    created = call(
        workbench,
        "POST",
        "/api/v1/admin/evidence-requests",
        workbench.admin_token,
        json={"user_id": user.id, "request_type": "常规", "application_number": "QZ-COMMIT"},
        expected=201,
    ).json()
    path = f"/api/v1/admin/evidence-requests/{created['id']}"
    call(workbench, "POST", path + "/accept", workbench.admin_token)
    call(workbench, "POST", path + "/quote", workbench.admin_token, json={"amount": 420})
    visible = []

    def observe(body):
        with Session(workbench.session.get_bind()) as reader:
            row = reader.get(EvidenceRequest, body["id"])
            visible.append((row.status, row.payment_amount))

    client = TestClient(ObserveResponse(workbench.app, observe))
    response = client.post(
        path + "/confirm-payment", headers=auth(workbench.admin_token), json={"amount": 420}
    )
    assert response.status_code == 200
    assert visible == [("取证中", 420)]
