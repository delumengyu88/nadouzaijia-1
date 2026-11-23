import pytest
from sqlalchemy import event

from nadou.db import Database
from nadou.main import create_app
from nadou.models import IndexRecord
from nadou.signed_url import FakeSignedUrlProvider
from tests.test_search import add_hit, add_user, at_age
from tests.user_helpers import UserClient

BATCH = "/api/v1/programs/search/batch"


@pytest.mark.parametrize("tier", [None, "vip", "regular", "program_30", "screenshot"])
def test_batch_matches_single_responses_in_input_order(session, clock, tier):
    # The signer captures its clock at construction; inject it before any HTTP requests.
    app = create_app(
        database=Database(engine=session.get_bind()),
        now=lambda: clock,
        urls=FakeSignedUrlProvider(now=clock.timestamp),
    )
    client = UserClient(app, session, clock)
    names = ["甲片", "其它片", "怪", "　甲片 ", "未收录片", "乙片"]
    for index, (name, age) in enumerate(
        [("甲片", 29), ("甲片", 30), ("其它片", 90), ("怪", 180), ("乙片", 200)]
    ):
        add_hit(
            session,
            task_id=f"t-{index}",
            record_id=f"r-{index}",
            program=name,
            when=at_age(clock, age),
        )
    session.flush()
    session.get(IndexRecord, "r-4").hit_type = "dispute"
    add_user(session, "purchaser", clock, tier, program="甲片", record_id="r-2")
    session.commit()
    headers = client.auth("purchaser")
    expected = [
        client.get("/api/v1/programs/search", params={"name": n}, headers=headers).json()
        for n in names
    ]
    selects = []

    def count(_conn, _cursor, statement, *_args):
        if "FROM index_records" in statement:
            selects.append(statement)

    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", count)
    try:
        res = client.post(BATCH, json={"names": names}, headers=headers)
    finally:
        event.remove(engine, "before_cursor_execute", count)
    assert res.status_code == 200
    assert res.json() == expected
    assert res.json()[0] == res.json()[3]
    assert len(selects) == 1


def test_batch_bounds_and_empty_names(client):
    assert client.post(BATCH, json={"names": ["甲"] * 200}).status_code == 200
    for names in ([], ["甲"] * 201):
        assert client.post(BATCH, json={"names": names}).status_code == 422
    for empty in ("", "　 "):
        assert client.post(BATCH, json={"names": ["甲", empty]}).status_code == 400
    assert client.post(BATCH, json={"names": ["甲"]}, headers=client.auth(None)).status_code == 401


def test_saved_batch_is_atomic_owner_only_and_success_survives_late_failure(session, client, clock):
    add_user(session, "other", clock)
    session.commit()
    other = client.auth("other")
    names = ["影片甲", "影片乙"]
    qid = client.post("/api/v1/queries", json={"names": names}).json()["id"]
    payload = {"query_id": qid, "names": names}
    failure = f"/api/v1/queries/{qid}/results/failed"
    assert client.post(BATCH, json=payload, headers=other).status_code == 404
    assert client.post(failure, json={"names": names}, headers=other).status_code == 404
    assert (
        client.post(BATCH, json={**payload, "names": [names[0], "不属于记录"]}).status_code == 400
    )
    detail = f"/api/v1/queries/{qid}"
    assert client.get(detail).json()["queued"]
    assert client.post(failure, json={"names": names}).status_code == 200
    assert client.get(detail).json()["allFail"]
    assert client.post(BATCH, json=payload).status_code == 200
    assert client.post(failure, json={"names": names}).status_code == 200
    saved = client.get(detail).json()
    assert saved["done"] and all(item["missed"] for item in saved["items"])
