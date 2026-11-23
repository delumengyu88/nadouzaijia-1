"""Hit frames from W2's COS export: manifest, fallback key, caches, timeouts, wiring."""

import hashlib
import hmac
import json
import threading
import time
from io import BytesIO

import httpx
from openpyxl import load_workbook
from PIL import Image

from nadou.config import get_settings
from nadou.cos_frames import (
    CosFrameSource,
    CosObjectClient,
    CosResponse,
    DiskFrameCache,
    FrameManifest,
    FrameTarget,
)
from nadou.frames import FrameImage
from tests.test_query_export import (
    add_query,
    add_user,
    embedded_images,
    export,
    hit_table,
    make_client,
)
from tests.test_result_bands import PROGRAM, Busy, _detail, seed

PREFIX = "w2-export"


def jpeg(color=(30, 60, 90)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (960, 576), color).save(buffer, format="JPEG")
    return buffer.getvalue()


class FakeCos:
    """In-memory bucket. Records every GET; can fail or be slow on demand."""

    def __init__(self, objects=None, manifest=None, etag='"m1"'):
        self.objects = dict(objects or {})
        self.manifest = manifest
        self.etag = etag
        self.gets: list[str] = []
        self.fail = False
        self.delay = 0.0
        self.active = 0
        self.peak = 0
        self._lock = threading.Lock()

    def set_manifest(self, manifest, etag):
        self.manifest, self.etag = manifest, etag

    def get(self, key, *, if_none_match=""):
        with self._lock:
            self.gets.append(key)
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            if self.delay:
                time.sleep(self.delay)
            if self.fail:
                raise httpx.ConnectTimeout("boom")
            if key == f"{PREFIX}/frames_manifest.json":
                if self.manifest is None:
                    return CosResponse(404)
                if if_none_match and if_none_match == self.etag:
                    return CosResponse(304)
                return CosResponse(200, json.dumps(self.manifest).encode(), self.etag)
            if key in self.objects:
                return CosResponse(200, self.objects[key], '"x"')
            return CosResponse(404)
        finally:
            with self._lock:
                self.active -= 1


def manifest_of(*entries):
    return {
        "entries": [
            {"task_id": t, "program_name": n, "path": p, "ts": "", "frame": ""}
            for t, n, p in entries
        ],
        "done": {f"{t}|{n}": p for t, n, p in entries},
        "miss": {},
        "version": "v1",
    }


def target(task="t1", name=PROGRAM, raw=None, stamps=("00:14:16", "00:14:17")):
    return FrameTarget("r1", task, name, raw if raw is not None else name, tuple(stamps))


def source(cos, tmp_path, cap=10_000_000, lookup=None, clock=None):
    clock = clock or time.monotonic
    manifest = FrameManifest(cos, f"{PREFIX}/frames_manifest.json", now=clock)
    return CosFrameSource(
        cos,
        PREFIX,
        DiskFrameCache(tmp_path / "cache", cap),
        lookup=lookup,
        manifest=manifest,
        now=clock,
    )


def test_signature_follows_cos_v5_and_get_maps_statuses():
    seen = {}

    def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["inm"] = request.headers.get("if-none-match")
        if request.url.path.endswith("missing.jpg"):
            return httpx.Response(404)
        return httpx.Response(200, content=b"abc", headers={"ETag": '"e1"'})

    client = CosObjectClient(
        "AKIDtest",
        "secret",
        "bkt-123",
        "ap-x",
        transport=httpx.MockTransport(handler),
        now=lambda: 1_700_000_060,
    )
    got = client.get("p/frames/t1/00-14-16.jpg", if_none_match='"e0"')
    assert (got.status, got.body, got.etag) == (200, b"abc", '"e1"')
    assert seen["url"] == "https://bkt-123.cos.ap-x.myqcloud.com/p/frames/t1/00-14-16.jpg"
    assert seen["inm"] == '"e0"'
    key_time = "1700000000;1700000600"
    sign_key = hmac.new(b"secret", key_time.encode(), hashlib.sha1).hexdigest()
    http_string = "get\n/p/frames/t1/00-14-16.jpg\n\nhost=bkt-123.cos.ap-x.myqcloud.com\n"
    to_sign = f"sha1\n{key_time}\n{hashlib.sha1(http_string.encode()).hexdigest()}\n"
    signature = hmac.new(sign_key.encode(), to_sign.encode(), hashlib.sha1).hexdigest()
    assert seen["auth"] == (
        f"q-sign-algorithm=sha1&q-ak=AKIDtest&q-sign-time={key_time}&q-key-time={key_time}"
        f"&q-header-list=host&q-url-param-list=&q-signature={signature}"
    )
    missing = client.get("p/missing.jpg")
    assert (missing.status, missing.body) == (404, b"")


def test_manifest_maps_raw_and_folded_titles():
    raw = json.dumps(manifest_of(("t1", "夏目友人帐 ", "w2-export/frames/t1/00-14-16.jpg")))
    mapping = FrameManifest.parse(raw.encode())
    assert mapping[("t1", "夏目友人帐 ")] == "w2-export/frames/t1/00-14-16.jpg"
    assert ("t1", "夏目友人帐") in mapping
    # The bare list form is accepted too.
    listed = FrameManifest.parse(
        json.dumps([{"task_id": "t2", "program_name": "a", "path": "k"}]).encode()
    )
    assert listed[("t2", "a")] == "k"


def test_manifest_key_first_then_first_timestamp_fallback(tmp_path):
    cos = FakeCos(
        objects={
            f"{PREFIX}/frames/t1/00-20-00.jpg": jpeg((1, 2, 3)),
            f"{PREFIX}/frames/t2/01-02-03.jpg": jpeg((4, 5, 6)),
        },
        manifest=manifest_of(("t1", PROGRAM, f"{PREFIX}/frames/t1/00-20-00.jpg")),
    )
    frames = source(cos, tmp_path)
    frames.manifest.refresh_now()
    assert frames.keys_for(target()) == [
        f"{PREFIX}/frames/t1/00-20-00.jpg",
        f"{PREFIX}/frames/t1/00-14-16.jpg",
    ]
    assert frames.fetch_target(target()) == cos.objects[f"{PREFIX}/frames/t1/00-20-00.jpg"]
    # Not in the manifest: frames/<task>/<HH-MM-SS>.jpg of the first timestamp.
    other = target(task="t2", stamps=("1:02:03", "01:02:04"))
    assert frames.keys_for(other) == [f"{PREFIX}/frames/t2/01-02-03.jpg"]
    assert frames.fetch_target(other) == cos.objects[f"{PREFIX}/frames/t2/01-02-03.jpg"]
    # No timestamps, odd timestamps or unsafe task ids give no key at all.
    assert frames.keys_for(target(task="t3", stamps=())) == []
    assert frames.keys_for(target(task="t3", stamps=("abc",))) == []
    assert frames.keys_for(target(task="../x", stamps=("00:00:01",))) == []


def test_missing_frame_raises_unavailable_and_is_remembered(tmp_path):
    from nadou.frames import FrameUnavailable

    cos = FakeCos(manifest=manifest_of())
    frames = source(cos, tmp_path)
    for _ in range(3):
        try:
            frames.fetch_target(target())
        except FrameUnavailable:
            pass
        else:
            raise AssertionError("expected FrameUnavailable")
    assert cos.gets.count(f"{PREFIX}/frames/t1/00-14-16.jpg") == 1


def test_manifest_refreshes_on_etag_and_survives_failures(tmp_path):
    now = [0.0]
    cos = FakeCos(manifest=manifest_of(("t1", PROGRAM, "w2-export/frames/t1/a.jpg")))
    manifest = FrameManifest(
        cos,
        f"{PREFIX}/frames_manifest.json",
        refresh_seconds=600,
        retry_seconds=60,
        now=lambda: now[0],
    )
    calls = lambda: cos.gets.count(f"{PREFIX}/frames_manifest.json")  # noqa: E731

    def settle():
        for _ in range(200):
            if not manifest._refreshing:
                return
            time.sleep(0.01)

    # The first lookup does not wait: it starts the load and answers from what it has.
    assert manifest.lookup(target()) is None
    settle()
    assert manifest.lookup(target()) == "w2-export/frames/t1/a.jpg"
    assert calls() == 1
    now[0] = 601  # due: checked in the background with the ETag, 304 keeps the entries
    assert manifest.lookup(target()) == "w2-export/frames/t1/a.jpg"
    settle()
    assert calls() == 2
    cos.set_manifest(manifest_of(("t1", PROGRAM, "w2-export/frames/t1/b.jpg")), '"m2"')
    now[0] = 1300
    manifest.lookup(target())  # serves the old entries while the new one downloads
    settle()
    assert manifest.lookup(target()) == "w2-export/frames/t1/b.jpg"
    cos.fail = True
    now[0] = 2000
    manifest.lookup(target())
    settle()
    assert manifest.lookup(target()) == "w2-export/frames/t1/b.jpg"


def test_first_manifest_failure_still_uses_the_timestamp_key(tmp_path):
    cos = FakeCos(objects={f"{PREFIX}/frames/t1/00-14-16.jpg": jpeg()}, manifest=None)
    frames = source(cos, tmp_path)
    assert frames.fetch_target(target()) == cos.objects[f"{PREFIX}/frames/t1/00-14-16.jpg"]


def test_disk_cache_serves_repeats_and_stays_under_its_cap(tmp_path):
    objects = {f"{PREFIX}/frames/t{i}/00-00-01.jpg": jpeg((i, i, i)) for i in range(6)}
    size = len(next(iter(objects.values())))
    cos = FakeCos(objects=objects, manifest=manifest_of())
    frames = source(cos, tmp_path, cap=size * 3)
    first = frames.fetch_target(target(task="t0", stamps=("00:00:01",)))
    again = frames.fetch_target(target(task="t0", stamps=("00:00:01",)))
    assert first == again
    assert cos.gets.count(f"{PREFIX}/frames/t0/00-00-01.jpg") == 1
    for i in range(1, 6):
        frames.fetch_target(target(task=f"t{i}", stamps=("00:00:01",)))
    files = list((tmp_path / "cache").glob("*.jpg"))
    assert sum(f.stat().st_size for f in files) <= size * 3
    assert 1 <= len(files) <= 3


def test_cos_failure_degrades_to_404_never_500(session, clock, tmp_path):
    seed(session, clock)
    add_user(session, "vip", clock, "vip")
    session.commit()
    cos = FakeCos(manifest=manifest_of())
    cos.fail = True
    client = make_client(session, clock, frames=source(cos, tmp_path))
    response = client.get("/api/v1/records/b-rec-0/frame", headers=client.auth("vip"))
    assert response.status_code == 404


def test_frame_endpoint_clear_for_members_blurred_for_free_404_without_image(
    session, clock, tmp_path
):
    seed(session, clock)
    add_user(session, "free", clock)
    add_user(session, "vip", clock, "vip")
    session.commit()
    busy = Busy().read("card", "x")
    # b-rec-0 via the manifest, b-rec-1 via the timestamp fallback, b-rec-2 has no image.
    cos = FakeCos(
        objects={
            f"{PREFIX}/frames/b-task-0/frame-a.jpg": busy,
            f"{PREFIX}/frames/b-task-1/00-01-02.jpg": busy,
        },
        manifest=manifest_of(("b-task-0", PROGRAM, f"{PREFIX}/frames/b-task-0/frame-a.jpg")),
    )
    frames = source(cos, tmp_path)
    frames.manifest.refresh_now()
    client = make_client(session, clock, frames=frames)
    vip = client.get("/api/v1/records/b-rec-0/frame", headers=client.auth("vip"))
    free = client.get("/api/v1/records/b-rec-0/frame", headers=client.auth("free"))
    assert vip.status_code == free.status_code == 200
    assert vip.headers["X-Nadou-Frame"] == "clear" and free.headers["X-Nadou-Frame"] == "blur"
    assert vip.content == FrameImage.prepare(busy).jpeg
    assert _detail(free.content) < _detail(vip.content) / 4
    assert (
        client.get("/api/v1/records/b-rec-1/frame", headers=client.auth("vip")).status_code == 200
    )
    missing = client.get("/api/v1/records/b-rec-2/frame", headers=client.auth("vip"))
    assert missing.status_code == 404 and missing.json()["detail"] == "暂无截图"
    body = client.get(
        "/api/v1/programs/search", params={"name": PROGRAM}, headers=client.auth("vip")
    ).json()
    assert body["frames_ready"] is True


def test_report_embeds_cos_frames_in_parallel_and_marks_the_rest(session, clock, tmp_path):
    seed(session, clock)
    add_user(session, "vip", clock, "vip")
    add_query(
        session,
        clock,
        "q-vip",
        [{"uploaded": PROGRAM, "matched": PROGRAM, "resolution": "exact"}],
        user_id="vip",
    )
    session.commit()
    busy = Busy().read("card", "x")
    objects = {f"{PREFIX}/frames/b-task-{i}/00-01-02.jpg": busy for i in range(5)}
    cos = FakeCos(objects=objects, manifest=manifest_of())
    cos.delay = 0.05
    client = make_client(session, clock, frames=source(cos, tmp_path))
    response = export(client, "q-vip", "vip")
    assert response.status_code == 200
    sheet = load_workbook(BytesIO(response.content))["命中结果"]
    _headers, rows, _ = hit_table(sheet)
    assert len(rows) == 7
    assert sum(1 for row in rows if "暂无截图" in row) == 2
    assert len(embedded_images(response.content)) == 5
    assert cos.peak > 1
    # A second report comes from the disk cache; the two misses are remembered briefly.
    before = len([k for k in cos.gets if k.endswith(".jpg")])
    again = export(client, "q-vip", "vip")
    assert len(embedded_images(again.content)) == 5
    assert len([k for k in cos.gets if k.endswith(".jpg")]) == before


def test_deployed_app_uses_cos_only_when_configured(session, clock, monkeypatch, tmp_path):
    import nadou.cos_frames as cos_frames
    from nadou.db import Database
    from nadou.main import create_app
    from tests.user_helpers import UserClient

    monkeypatch.setenv("NADOU_DATA_SOURCE", "export")
    get_settings.cache_clear()
    add_user(session, "vip", clock, "vip")
    session.commit()

    def app():
        return UserClient(
            create_app(database=Database(engine=session.get_bind()), now=lambda: clock),
            session,
            clock,
        )

    def ready(client):
        return client.get(
            "/api/v1/programs/search", params={"name": PROGRAM}, headers=client.auth("vip")
        ).json()["frames_ready"]

    assert ready(app()) is False
    built = []

    class Fake(FakeCos):
        def __init__(self, *args, **kwargs):
            built.append(args)
            super().__init__(manifest=manifest_of())

    monkeypatch.setattr(cos_frames, "CosObjectClient", Fake)
    for name, value in {
        "NADOU_FRAMES_COS_SECRET_ID": "id",
        "NADOU_FRAMES_COS_SECRET_KEY": "key",
        "NADOU_FRAMES_COS_BUCKET": "bucket",
        "NADOU_FRAMES_COS_REGION": "region",
        "NADOU_FRAMES_COS_PREFIX": PREFIX,
        "NADOU_FRAMES_CACHE_DIR": str(tmp_path / "c"),
    }.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    client = app()
    assert ready(client) is True
    assert built == [("id", "key", "bucket", "region")]
    assert client.get("/api/v1/records/nope/frame", headers=client.auth("vip")).status_code == 404
    # One value missing: not configured, behaves exactly as before.
    monkeypatch.setenv("NADOU_FRAMES_COS_PREFIX", "")
    get_settings.cache_clear()
    assert ready(app()) is False
    get_settings.cache_clear()


def test_report_prefetch_stops_at_its_budget_and_finishes_in_the_cache(tmp_path):
    objects = {f"{PREFIX}/frames/t{i}/00-00-01.jpg": jpeg((i, i, i)) for i in range(4)}
    cos = FakeCos(objects=objects, manifest=manifest_of())
    cos.delay = 0.4
    frames = source(cos, tmp_path)

    class Rec:
        def __init__(self, i):
            self.id, self.task_id = f"r{i}", f"t{i}"
            self.program_name = self.program_name_raw = PROGRAM
            self.timestamps = ["00:00:01"]

    started = time.monotonic()
    found = frames.prefetch([Rec(i) for i in range(4)], budget_seconds=0.1)
    assert time.monotonic() - started < 0.35
    assert found == {}
    time.sleep(1.0)
    cos.delay = 0
    assert len(frames.prefetch([Rec(i) for i in range(4)])) == 4
    assert sum(1 for key in cos.gets if key.endswith(".jpg")) == 4
