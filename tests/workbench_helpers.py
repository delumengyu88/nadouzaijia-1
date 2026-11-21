from datetime import timedelta
from uuid import uuid4

from nadou.models import Entitlement, Order, OrderIssuance, Task, User


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def call(workbench, method: str, path: str, token: str | None, *, json=None, expected: int = 200):
    headers = {} if token is None else auth(token)
    kwargs = {"headers": headers}
    if json is not None:
        kwargs["json"] = json
    response = workbench.client.request(method, path, **kwargs)
    assert response.status_code == expected, response.text
    if response.headers.get("content-type", "").startswith("application/json"):
        return response
    return response


def add_user(session, clock, phone: str, *, vip: bool = False) -> User:
    user = User(id=str(uuid4()), created_at=clock, phone=phone)
    session.add(user)
    if vip:
        session.add(
            Entitlement(
                user_id=user.id,
                kind="vip",
                program_name=None,
                record_id=None,
                expires_at=clock + timedelta(days=30),
                source=None,
            )
        )
    session.commit()
    return user


def add_order(
    session,
    user: User,
    clock,
    number: str,
    *,
    status: str = "发放异常",
    tier: str = "vip",
    days: int = 30,
    issuance: str | None = "发放异常",
) -> Order:
    order = Order(
        id=str(uuid4()),
        order_number=number,
        user_id=user.id,
        tier=tier,
        duration_days=days,
        amount=80,
        status=status,
        created_at=clock,
    )
    session.add(order)
    session.flush()
    if issuance is not None:
        session.add(
            OrderIssuance(
                order_id=order.id,
                status=issuance,
                attempts=3 if issuance == "发放异常" else 1,
                tier=tier,
                duration_days=days,
                entitlement_id=None,
                last_error="gateway down" if issuance == "发放异常" else None,
                reissued=False,
                created_at=clock,
                updated_at=clock,
            )
        )
    session.commit()
    return order


def add_unverified_task(session, task_id: str, network: str | None = "公网") -> Task:
    task = Task(
        task_id=task_id,
        status="done",
        raw_filename="unknown.mp4",
        network_type=network,
        platform_name=None,
        source_unverified=True,
        is_test=False,
    )
    session.add(task)
    session.commit()
    return task
