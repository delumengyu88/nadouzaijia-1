"""Result-page age bands, one-program band unlocks, server-side blur and report band rows."""

from datetime import timedelta
from io import BytesIO

from openpyxl import load_workbook
from PIL import Image, ImageFilter, ImageStat

from nadou.config import get_settings
from nadou.frames import FrameImage, SignedDerivedFrameSource
from nadou.models import Entitlement, Order, OrderIssuance, User
from nadou.signed_url import FakeSignedUrlProvider
from tests.test_query_export import (
    COLUMNS,
    add_query,
    add_record,
    add_user,
    at_age,
    band_rows,
    embedded_images,
    export,
    hit_table,
    make_client,
)
from tests.test_virtual_pay import payment, signed_event, virtual_setup  # noqa: F401

PROGRAM = "夏目友人帐"
# ge_180 ×2, d90_179 ×3, d30_89 ×2, lt_30 ×1.
AGES = (400, 200, 150, 120, 95, 70, 40, 10)


def seed(session, clock, program=PROGRAM, prefix="b"):
    for index, age in enumerate(AGES):
        add_record(
            session,
            task_id=f"{prefix}-task-{index}",
            record_id=f"{prefix}-rec-{index}",
            program=program,
            when=at_age(clock, age),
            network="专网" if index % 2 else "公网",
        )


def bands(client, user_id, program=PROGRAM):
    body = client.get(
        "/api/v1/programs/search", params={"name": program}, headers=client.auth(user_id)
    ).json()
    return body, {row["band"]: row for row in body["bands"]}


def grant(session, user_id, kind, clock, program=None, days=30):
    session.add(
        Entitlement(
            user_id=user_id,
            kind=kind,
            program_name=program,
            expires_at=clock + timedelta(days=days),
            source="order",
        )
    )


def test_band_visibility_per_tier(session, clock):
    seed(session, clock)
    add_user(session, "free", clock)
    add_user(session, "regular", clock, "regular")
    add_user(session, "vip", clock, "vip")
    # 体验会员 is sold as a 5-day VIP entitlement.
    add_user(session, "trial", clock)
    grant(session, "trial", "vip", clock, days=5)
    session.commit()
    client = make_client(session, clock)

    body, free = bands(client, "free")
    assert body["viewer_tier"] == "free"
    assert [row["band"] for row in body["bands"]] == ["ge_180", "d90_179", "d30_89", "lt_30"]
    assert [row["label"] for row in body["bands"]] == ["180天以上", "90–180天", "30–90天", "30天内"]
    assert {key: row["count"] for key, row in free.items()} == {
        "ge_180": 2,
        "d90_179": 3,
        "d30_89": 2,
        "lt_30": 1,
    }
    assert (free["ge_180"]["state"], free["ge_180"]["frame_view"]) == ("open", "blur")
    assert free["ge_180"]["unlocked_by"] == "free"
    assert free["d90_179"]["state"] == free["d30_89"]["state"] == "locked"
    assert free["d90_179"]["unlock"]["product"] == "unlock_band_90_180"
    assert (free["d90_179"]["unlock"]["amount_fen"], free["d90_179"]["unlock"]["price_text"]) == (
        1990,
        "¥19.9",
    )
    assert free["d30_89"]["unlock"]["product"] == "unlock_band_30_90"
    assert free["d30_89"]["unlock"]["price_text"] == "¥39.9"
    # No WeChat item configured and no openid: shown with a price, not purchasable.
    assert free["d90_179"]["unlock"]["purchasable"] is False
    assert free["lt_30"]["state"] == "evidence" and "unlock" not in free["lt_30"]
    open_rows = [r for g in body["platform_groups"] for r in g["records"]]
    assert {r["age_bucket"] for r in open_rows} == {"ge_180"}
    assert {r["frame_view"] for r in open_rows} == {"blur"}

    body, regular = bands(client, "regular")
    assert body["viewer_tier"] == "regular"
    assert regular["ge_180"]["frame_view"] == regular["d90_179"]["frame_view"] == "clear"
    assert regular["d90_179"]["unlocked_by"] == "membership"
    assert regular["d30_89"]["state"] == "locked"
    assert regular["d30_89"]["unlock"]["product"] == "unlock_band_30_90"
    assert regular["d30_89"]["upgrade_hint"] == "升级 VIP 可查看全部 30 天以上记录"
    assert "unlock" not in regular["d90_179"]

    for user_id in ("vip", "trial"):
        body, vip = bands(client, user_id)
        assert body["viewer_tier"] == "vip"
        assert [vip[key]["state"] for key in ("ge_180", "d90_179", "d30_89")] == ["open"] * 3
        assert all("unlock" not in vip[key] for key in vip)
        assert vip["lt_30"]["state"] == "evidence"
        open_rows = [r for g in body["platform_groups"] for r in g["records"]]
        assert len(open_rows) == 7
        assert {r["frame_view"] for r in open_rows} == {"clear"}
        # 30天内 never shows details, whatever the tier.
        assert all(r["age_bucket"] != "lt_30" for r in open_rows)


def test_band_unlock_opens_one_band_of_one_program_permanently(session, clock):
    seed(session, clock)
    seed(session, clock, program="另一个节目", prefix="o")
    add_user(session, "buyer", clock)
    grant(session, "buyer", "band_90_180", clock, program=PROGRAM, days=36500)
    session.commit()
    client = make_client(session, clock)

    body, mine = bands(client, "buyer")
    assert body["viewer_tier"] == "free"
    assert mine["d90_179"]["state"] == "open"
    assert mine["d90_179"]["unlocked_by"] == "unlock"
    assert mine["d90_179"]["frame_view"] == "clear"
    assert mine["ge_180"]["frame_view"] == "blur"
    assert mine["d30_89"]["state"] == "locked"
    open_rows = [r for g in body["platform_groups"] for r in g["records"]]
    assert sorted(r["age_bucket"] for r in open_rows).count("d90_179") == 3

    _body, other = bands(client, "buyer", "另一个节目")
    assert other["d90_179"]["state"] == "locked"

    # The 30–90天 unlock does not open 90–180天 for a free user.
    add_user(session, "buyer-30", clock)
    grant(session, "buyer-30", "band_30_90", clock, program=PROGRAM, days=36500)
    session.commit()
    _body, only_30 = bands(client, "buyer-30")
    assert only_30["d30_89"]["state"] == "open"
    assert only_30["d90_179"]["state"] == "locked"


def test_unlock_order_grants_only_after_confirmed_payment(virtual_setup, session, monkeypatch):  # noqa: F811
    from nadou.payments import xpay

    client = virtual_setup
    clock = client.clock
    seed(session, clock)
    session.commit()
    from pydantic import SecretStr

    cfg = get_settings()
    monkeypatch.setattr(cfg, "wx_appid", "test-app")
    monkeypatch.setattr(cfg, "wx_appsecret", SecretStr("s" * 32))
    monkeypatch.setattr(
        cfg,
        "xpay_products",
        {
            "membership_vip": "official-product",
            "unlock_band_90_180": "item-band-90-180",
            "unlock_band_30_90": "item-band-30-90",
        },
    )

    offer = client.get(
        "/api/v1/unlocks/offer", params={"product": "unlock_band_90_180", "program_name": PROGRAM}
    )
    assert offer.status_code == 200, offer.text
    assert offer.json()["count"] == 3 and offer.json()["purchasable"] is True
    assert offer.json()["band_label"] == "90–180天" and offer.json()["price_text"] == "¥19.9"
    assert "product_id" not in offer.json()

    # A program is required, and a band with nothing in it cannot be bought.
    missing = client.post("/api/v1/xpay/orders", json={"tier": "unlock_band_90_180", "code": "c"})
    assert missing.status_code == 422
    empty = client.post(
        "/api/v1/xpay/orders",
        json={"tier": "unlock_band_90_180", "code": "c", "program_name": "没有这个节目"},
    )
    assert empty.status_code == 409

    created = client.post(
        "/api/v1/xpay/orders",
        json={"tier": "unlock_band_90_180", "code": "c", "program_name": PROGRAM},
    )
    assert created.status_code == 200, created.text
    number = created.json()["order_number"]
    assert created.json()["amount_fen"] == 1990
    assert created.json()["permanent"] is True and created.json()["program_name"] == PROGRAM
    order = session.query(Order).filter_by(order_number=number).one()
    assert (order.tier, order.product, order.program_name) == (
        "band_90_180",
        "unlock_band_90_180",
        PROGRAM,
    )
    # Not paid yet: nothing is granted and the band stays locked.
    assert session.query(Entitlement).count() == 0
    _body, before = bands(client, "test-default-free")
    assert before["d90_179"]["state"] == "locked"

    remote = payment(number, price=1990)
    monkeypatch.setattr(xpay, "query_order", lambda env, openid, merchant: remote)
    delivered = {
        "Event": "xpay_goods_deliver_notify",
        "OpenId": "virtual-owner",
        "Env": 0,
        "OutTradeNo": number,
        "GoodsInfo": {
            "ProductId": "item-band-90-180",
            "Quantity": 1,
            "ActualPrice": 1990,
            "OrigPrice": 1990,
            "Attach": "unlock_band_90_180",
        },
    }
    wrong_price = {**delivered, "GoodsInfo": {**delivered["GoodsInfo"], "ActualPrice": 1}}
    assert signed_event(client, wrong_price, monkeypatch).status_code == 409
    assert session.query(Entitlement).count() == 0
    for _ in range(2):
        assert signed_event(client, delivered, monkeypatch).text == "success"
    session.expire_all()
    grants = session.query(Entitlement).all()
    assert len(grants) == 1 and session.query(OrderIssuance).count() == 1
    assert (grants[0].kind, grants[0].program_name) == ("band_90_180", PROGRAM)
    assert grants[0].expires_at - clock >= timedelta(days=36000)
    _body, after = bands(client, "test-default-free")
    assert after["d90_179"]["state"] == "open"
    assert after["d30_89"]["state"] == "locked"
    # Already open: a second purchase of the same band is refused.
    again = client.post(
        "/api/v1/xpay/orders",
        json={"tier": "unlock_band_90_180", "code": "c", "program_name": PROGRAM},
    )
    assert again.status_code == 409


def test_unlock_needs_a_configured_item(virtual_setup, session):  # noqa: F811
    client = virtual_setup
    seed(session, client.clock)
    session.commit()
    offer = client.get(
        "/api/v1/unlocks/offer", params={"product": "unlock_band_30_90", "program_name": PROGRAM}
    ).json()
    assert offer["purchasable"] is False
    result = client.post(
        "/api/v1/xpay/orders",
        json={"tier": "unlock_band_30_90", "code": "c", "program_name": PROGRAM},
    )
    assert result.status_code == 503
    assert session.query(Order).count() == 0


def _detail(jpeg: bytes) -> float:
    """Edge energy: a blurred picture has almost none."""
    image = Image.open(BytesIO(jpeg)).convert("L")
    return ImageStat.Stat(image.filter(ImageFilter.FIND_EDGES)).mean[0]


class Busy:
    """A frame with fine stripes so blur is measurable."""

    def read(self, variant, record_id):
        image = Image.new("RGB", (960, 540), (20, 40, 90))
        pixels = image.load()
        for x in range(0, 960, 6):
            for y in range(540):
                pixels[x, y] = (250, 250, 250)
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()


def test_frame_is_blurred_on_the_server_for_free_users(session, clock):
    seed(session, clock)
    add_user(session, "free", clock)
    add_user(session, "vip", clock, "vip")
    session.commit()
    urls = FakeSignedUrlProvider()
    frames = SignedDerivedFrameSource(urls, Busy())
    client = make_client(session, clock, frames=frames, urls=urls)

    free = client.get("/api/v1/records/b-rec-0/frame", headers=client.auth("free"))
    vip = client.get("/api/v1/records/b-rec-0/frame", headers=client.auth("vip"))
    assert free.status_code == vip.status_code == 200
    assert free.headers["X-Nadou-Frame"] == "blur" and vip.headers["X-Nadou-Frame"] == "clear"
    assert free.headers["Cache-Control"] == "private, no-store"
    assert free.content != vip.content
    clear = FrameImage.prepare(Busy().read("card", "b-rec-0")).jpeg
    assert vip.content == clear
    assert _detail(free.content) < _detail(vip.content) / 4

    # Locked band, 30天内, unknown record and anonymous callers get no picture.
    assert client.get("/api/v1/records/b-rec-3/frame", headers=client.auth("free")).status_code == (
        403
    )
    assert (
        client.get("/api/v1/records/b-rec-7/frame", headers=client.auth("vip")).status_code == 403
    )
    assert client.get("/api/v1/records/nope/frame", headers=client.auth("vip")).status_code == 404
    assert client.get(
        "/api/v1/records/b-rec-0/frame", headers={"Authorization": ""}
    ).status_code == (401)
    body = client.get(
        "/api/v1/programs/search", params={"name": PROGRAM}, headers=client.auth("free")
    ).json()
    assert body["frames_ready"] is True and body["report_ready"] is True


def test_frame_endpoint_without_a_frame_source(session, clock, monkeypatch):
    monkeypatch.setenv("NADOU_DATA_SOURCE", "export")
    get_settings.cache_clear()
    from nadou.db import Database
    from nadou.main import create_app
    from tests.user_helpers import UserClient

    add_user(session, "vip", clock, "vip")
    session.commit()
    client = UserClient(
        create_app(database=Database(engine=session.get_bind()), now=lambda: clock),
        session,
        clock,
    )
    response = client.get("/api/v1/records/whatever/frame", headers=client.auth("vip"))
    assert response.status_code == 404


def _query(session, clock, owner):
    add_query(
        session,
        clock,
        f"q-{owner}",
        [{"uploaded": PROGRAM, "matched": PROGRAM, "resolution": "exact", "program_type": "动漫"}],
        user_id=owner,
    )


def test_report_rows_follow_the_tier(session, clock):
    seed(session, clock)
    for owner, kind in (("free", None), ("regular", "regular"), ("vip", "vip")):
        add_user(session, owner, clock, kind)
        _query(session, clock, owner)
    add_user(session, "unlocked", clock)
    grant(session, "unlocked", "band_90_180", clock, program=PROGRAM, days=36500)
    _query(session, clock, "unlocked")
    session.commit()
    urls = FakeSignedUrlProvider()
    frames = SignedDerivedFrameSource(urls, Busy())
    client = make_client(session, clock, frames=frames, urls=urls)

    free = export(client, "q-free", "free")
    assert free.status_code == 200
    book = load_workbook(BytesIO(free.content))
    sheet = book["命中结果"]
    assert sheet["A2"].value == "那豆在架 命中结果（共导出 2 条）"
    headers, rows, _ = hit_table(sheet)
    assert headers == COLUMNS
    assert headers[2] == "网络类型" and headers[8] == "命中帧数"
    assert [row[0] for row in rows] == [1, 2]
    assert [row[2] for row in rows] == ["公网", "专网"]
    assert [row[8] for row in rows] == [1, 1]
    assert band_rows(sheet) == [
        (PROGRAM, "90–180 天 · 3 条 · 待解锁", "本节目单次解锁 ¥19.9，或开通会员后查看"),
        (PROGRAM, "30–90 天 · 2 条 · 待解锁", "本节目单次解锁 ¥39.9，或开通 VIP 后查看"),
        (PROGRAM, "30 天内 · 1 条 · 可委托取证", "不在线展示，可在小程序「委托取证」申请取证"),
    ]
    images = embedded_images(free.content)
    assert len(images) == 2
    clear = FrameImage.prepare(Busy().read("card", "x")).jpeg
    assert all(image != clear for image in images)
    assert all(_detail(image) < _detail(clear) / 4 for image in images)
    book.close()

    regular = load_workbook(BytesIO(export(client, "q-regular", "regular").content))["命中结果"]
    assert len(hit_table(regular)[1]) == 5
    assert [text for _name, text, _note in band_rows(regular)] == [
        "30–90 天 · 2 条 · 待解锁",
        "30 天内 · 1 条 · 可委托取证",
    ]

    vip_response = export(client, "q-vip", "vip")
    vip = load_workbook(BytesIO(vip_response.content))["命中结果"]
    assert len(hit_table(vip)[1]) == 7
    assert [text for _name, text, _note in band_rows(vip)] == ["30 天内 · 1 条 · 可委托取证"]
    assert all(image == clear for image in embedded_images(vip_response.content))

    unlocked_response = export(client, "q-unlocked", "unlocked")
    unlocked = load_workbook(BytesIO(unlocked_response.content))["命中结果"]
    assert len(hit_table(unlocked)[1]) == 5
    shots = embedded_images(unlocked_response.content)
    # Two blurred 180天以上 pictures, three clear unlocked 90–180天 pictures.
    assert sum(1 for image in shots if image == clear) == 3
    assert [text for _name, text, _note in band_rows(unlocked)][0] == "30–90 天 · 2 条 · 待解锁"


def test_report_layout_follows_the_template(session, clock):
    seed(session, clock)
    add_user(session, "free", clock)
    session.get(User, "free").display_name = "示例客户"
    _query(session, clock, "free")
    session.commit()
    client = make_client(session, clock)
    sheet = load_workbook(BytesIO(export(client, "q-free", "free").content))["命中结果"]
    assert sheet.row_dimensions[1].height == 9 and sheet.row_dimensions[6].height == 9
    assert sheet["A3"].value == "查询编号：q-free"
    assert sheet["F3"].value == "客户：示例客户"
    assert sheet["A4"].value == "查询范围：1 个节目 · 已收录平台 · ≥180 天记录"
    assert sheet["F4"].value.startswith("生成日期：")
    from nadou.query_export import HitWorkbook

    assert sheet["A5"].value == HitWorkbook.NOTE
    header = [sheet.cell(7, column).value for column in range(1, len(COLUMNS) + 1)]
    assert tuple(header) == COLUMNS
    assert sheet["A7"].fill.fgColor.rgb == "FF111111"
    assert sheet["A7"].font.color.rgb == "FFFFFFFF"
    assert sheet.freeze_panes == "A8"
    assert sheet.column_dimensions["J"].width == 35.25


def test_refund_closes_a_band_unlock(session, clock):
    from nadou.admin.audit import AuditWriter
    from nadou.admin.notify import FakeNotifier
    from nadou.admin.orders import EntitlementIssuer, OrderGrantWriter, OrderRefund

    seed(session, clock)
    add_user(session, "buyer", clock)
    order = Order(
        id="order-band",
        order_number="XVband0001",
        user_id="buyer",
        tier="band_30_90",
        duration_days=36500,
        amount=39,
        amount_fen=3990,
        product="unlock_band_30_90",
        status="已支付",
        created_at=clock,
        program_name=PROGRAM,
    )
    session.add(order)
    session.flush()
    EntitlementIssuer(session, OrderGrantWriter(), FakeNotifier(), clock).issue(order)
    session.commit()
    client = make_client(session, clock)
    assert bands(client, "buyer")[1]["d30_89"]["state"] == "open"
    OrderRefund(session, AuditWriter(session, clock, None, None), clock).refund(order, "测试退款")
    session.commit()
    assert order.status == "已退款"
    assert bands(client, "buyer")[1]["d30_89"]["state"] == "locked"
