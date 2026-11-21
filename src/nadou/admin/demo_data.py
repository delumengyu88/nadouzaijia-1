from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from nadou.admin.audit import AuditWriter
from nadou.admin.auth import StaffDirectory
from nadou.admin.content import AnnouncementBook, ExampleBook, SplashBook
from nadou.admin.evidence import EvidenceWorkflow
from nadou.admin.invites import InviteBook
from nadou.admin.members import MemberAdmin
from nadou.admin.notify import NullNotifier
from nadou.admin.orders import EntitlementIssuer, OrderAudit, OrderGrantWriter, OrderRefund
from nadou.models import (
    Entitlement,
    EvidenceRequest,
    IndexRun,
    InviteCode,
    Order,
    OrderIssuance,
    StaffAccount,
    StaffLoginLock,
    Task,
    User,
)
from nadou.records import IndexRecordStore
from nadou.seed import Seed


class DemoAccounts:
    ADMIN = "演示管理员"
    ADMIN_PASSWORD = "demo-pass-2026"
    SUPPORT = "演示客服"
    SUPPORT_PASSWORD = "demo-pass-2026"
    LOCKED = "演示锁定"
    LOCKED_PASSWORD = "demo-pass-2026"
    SAMPLE_PHONE = "13800002610"


class DemoData:
    """Obvious stand-ins for a local acceptance demo. Nothing here is a real person."""

    @staticmethod
    def already_loaded(session: Session) -> bool:
        found = session.scalar(
            select(StaffAccount.id).where(StaffAccount.username == DemoAccounts.ADMIN)
        )
        return found is not None

    @staticmethod
    def ensure_admin(session: Session, now) -> StaffAccount:
        existing = session.scalar(
            select(StaffAccount).where(StaffAccount.username == DemoAccounts.ADMIN)
        )
        if existing is not None:
            return existing
        return StaffDirectory.create(
            session,
            DemoAccounts.ADMIN,
            DemoAccounts.ADMIN_PASSWORD,
            "admin",
            now,
        )

    @staticmethod
    def load(session: Session, now) -> None:
        if DemoData.already_loaded(session):
            return
        Seed(session, now, allow_dev_user=True).run()
        DemoData._rename_unresolved(session)
        admin = DemoData.ensure_admin(session, now)
        support = StaffDirectory.create(
            session,
            DemoAccounts.SUPPORT,
            DemoAccounts.SUPPORT_PASSWORD,
            "support",
            now,
        )
        locked = StaffDirectory.create(
            session,
            DemoAccounts.LOCKED,
            DemoAccounts.LOCKED_PASSWORD,
            "support",
            now,
        )
        session.add(
            StaffLoginLock(
                account_id=locked.id,
                ip="127.0.0.1",
                consecutive_failures=5,
                locked_until=now + timedelta(minutes=30),
            )
        )
        audit = AuditWriter(session, now, admin, "local")
        users = DemoData._users(session, now, audit)
        DemoData._programs(session, now)
        DemoData._invites(session, now, admin, users)
        DemoData._orders(session, now, audit, users)
        DemoData._evidence(session, now, audit, support, users)
        DemoData._content(session, now, audit)
        DemoData._runs(session, now)
        session.flush()

    @staticmethod
    def _rename_unresolved(session: Session) -> None:
        names = {
            "unknown.mp4": "未标注平台.mp4",
            "1905.mp4": "早期无平台标注.mp4",
        }
        rows = session.scalars(select(Task)).all()
        for row in rows:
            if row.raw_filename in names:
                row.raw_filename = names[row.raw_filename]

    @staticmethod
    def _users(session: Session, now, audit: AuditWriter) -> dict[str, User]:
        specs = [
            ("vip", "赵闻", DemoAccounts.SAMPLE_PHONE, "o0000000000000a1", True),
            ("member", "林秋", "13900007082", "o0000000000000a2", False),
            ("free", "周安", "18600004419", "o0000000000000a3", False),
            ("banned", "沈清", "15000006631", "o0000000000000a4", False),
            ("paid", "何帆", "13700009054", "o0000000000000a5", False),
            ("grant", "许南", "18800002207", "o0000000000000a6", False),
        ]
        users: dict[str, User] = {}
        for key, name, phone, openid, vip in specs:
            user = User(
                id=str(uuid4()),
                created_at=now - timedelta(days=20),
                phone=phone,
                wechat_openid=openid,
                display_name=name,
            )
            session.add(user)
            users[key] = user
        session.flush()
        session.add(
            Entitlement(
                user_id=users["vip"].id,
                kind="vip",
                program_name=None,
                record_id=None,
                expires_at=now + timedelta(days=27),
                source="order",
            )
        )
        session.add(
            Entitlement(
                user_id=users["vip"].id,
                kind="regular",
                program_name=None,
                record_id=None,
                expires_at=now + timedelta(days=30),
                source="invite",
            )
        )
        session.add(
            Entitlement(
                user_id=users["member"].id,
                kind="regular",
                program_name=None,
                record_id=None,
                expires_at=now + timedelta(days=18),
                source="order",
            )
        )
        session.add(
            Entitlement(
                user_id=users["vip"].id,
                kind="program_30",
                program_name="星河旧梦",
                record_id=None,
                expires_at=now + timedelta(days=4),
                source="order",
            )
        )
        session.add(
            Entitlement(
                user_id=users["vip"].id,
                kind="screenshot",
                program_name="长街烟火",
                record_id=None,
                expires_at=now + timedelta(days=2),
                source="order",
            )
        )
        session.flush()
        MemberAdmin(session, audit).ban(
            users["banned"].id,
            "temporary",
            "短时大量异常检索，演示封禁",
            now + timedelta(days=30),
            now,
        )
        MemberAdmin(session, audit).grant(
            users["grant"].id,
            "vip",
            30,
            "示例渠道补发，演示手动开通",
            now,
        )
        return users

    @staticmethod
    def _programs(session: Session, now) -> None:
        store = IndexRecordStore(session)
        catalog = [
            ("demo-star", "星河旧梦", "广东移动", 12),
            ("demo-street", "长街烟火", "江苏电信", 9),
            ("demo-wind", "南风知我意", "浙江联通", 8),
            ("demo-sea", "海上生明月", "北京移动", 7),
        ]
        for task_id, program, platform, _hits in catalog:
            session.add(
                Task(
                    task_id=task_id,
                    status="done",
                    created_at=now - timedelta(days=40),
                    raw_filename=f"公网-{platform}-{program}-2026.10.01.mp4",
                    network_type="公网",
                    platform_name=platform,
                    source_unverified=False,
                    is_test=False,
                )
            )
            store.upsert(
                task_id=task_id,
                program_name=program,
                program_name_raw=program,
                hit_type="consensus",
                frame_count=3,
                timestamps=["00:01:02"],
                uploaded_at=now - timedelta(days=40),
            )
        session.add(
            Task(
                task_id="demo-parse-fail",
                status="done",
                created_at=now - timedelta(days=6),
                raw_filename="演示文件名缺失平台.mp4",
                network_type=None,
                platform_name=None,
                source_unverified=True,
                is_test=False,
            )
        )
        session.add(
            Task(
                task_id="demo-unlisted",
                status="done",
                created_at=now - timedelta(days=5),
                raw_filename="专网-2026.09.07.mp4",
                network_type="专网",
                platform_name=None,
                source_unverified=True,
                is_test=False,
            )
        )
        session.flush()

    @staticmethod
    def _invites(session: Session, now, admin: StaffAccount, users: dict[str, User]) -> None:
        book = InviteBook(session, AuditWriter(session, now, admin, "local"), now)
        created = book.generate(4, "临江示例律所", now + timedelta(days=30), admin)
        book.generate(2, "春季示例会", now + timedelta(days=20), admin)
        voided = book.generate(1, "临江示例律所", now + timedelta(days=30), admin)[0]
        book.void(voided.id, admin)
        redeemed = created[0]
        redeemed.redeemed_by = users["vip"].id
        redeemed.redeemed_at = now - timedelta(days=10)
        second = created[1]
        second.redeemed_by = users["paid"].id
        second.redeemed_at = now - timedelta(days=8)
        expired = created[2]
        expired.expires_at = now - timedelta(days=1)
        session.add(
            InviteCode(
                id=str(uuid4()),
                code="K7M3NQ2P",
                channel="春季示例会",
                created_by=admin.id,
                created_at=now - timedelta(days=40),
                expires_at=now - timedelta(days=2),
                redeemed_by=users["member"].id,
                redeemed_at=now - timedelta(days=35),
                voided_at=None,
                voided_by=None,
            )
        )
        session.flush()

    @staticmethod
    def _orders(session: Session, now, audit: AuditWriter, users: dict[str, User]) -> None:
        def add(number, user, status, tier, amount, client, days_ago, issuance, attempts):
            order = Order(
                id=str(uuid4()),
                order_number=number,
                user_id=user.id,
                tier=tier,
                duration_days=30,
                amount=amount,
                status=status,
                created_at=now - timedelta(days=days_ago),
                client=client,
                paid_at=None if status == "待支付" else now - timedelta(days=days_ago),
                refunded_at=None,
                refund_reason=None,
            )
            session.add(order)
            session.flush()
            if issuance is not None:
                session.add(
                    OrderIssuance(
                        order_id=order.id,
                        status=issuance,
                        attempts=attempts,
                        tier=tier,
                        duration_days=30,
                        entitlement_id=None,
                        last_error="权益写入失败" if issuance == "发放异常" else None,
                        reissued=False,
                        created_at=order.created_at,
                        updated_at=now,
                    )
                )
            return order

        broken = add(
            "VP20261122014", users["member"], "发放异常", "vip", 599, "iOS", 0, "发放异常", 3
        )
        add("VP20261121118", users["vip"], "发放异常", "regular", 299, "安卓", 1, "发放异常", 3)
        add("VP20261121002", users["free"], "待支付", "regular", 299, "安卓", 0, None, 0)
        issued = add("VP20261120088", users["paid"], "已支付", "vip", 599, "iOS", 3, "pending", 0)
        EntitlementIssuer(session, OrderGrantWriter(), NullNotifier(), now).issue(issued)
        replay = add(
            "VP20261118021", users["grant"], "发放异常", "vip", 599, "iOS", 2, "发放异常", 3
        )
        before = replay.status
        issuer = EntitlementIssuer(session, OrderGrantWriter(), NullNotifier(), now)
        recovered = issuer.reissue(replay)
        OrderAudit.reissue(audit, replay, before, recovered)
        paid = add("VP20261109004", users["vip"], "已发放", "vip", 599, "安卓", 12, None, 0)
        grant = OrderGrantWriter().write(session, paid.user_id, paid.tier, paid.duration_days, now)
        session.add(
            OrderIssuance(
                order_id=paid.id,
                status="issued",
                attempts=1,
                tier=paid.tier,
                duration_days=paid.duration_days,
                entitlement_id=grant.id,
                last_error=None,
                reissued=False,
                created_at=paid.created_at,
                updated_at=now,
            )
        )
        session.flush()
        OrderRefund(session, audit, now).refund(paid, "演示退款，对应权益已收回")
        del broken

    @staticmethod
    def _evidence(session, now, audit, support: StaffAccount, users: dict[str, User]) -> None:
        flow = EvidenceWorkflow(session, NullNotifier(), audit)
        fresh = flow.create(users["free"].id, "常规", now, "QZ20261121019")
        fresh.program_name = "南风知我意"
        fresh.platform_label = "浙江联通 · 公网"
        fresh.contact_name = "周安"
        fresh.requirement = "需要命中截图与时间戳。"
        accepted = flow.create(
            users["member"].id, "常规", now - timedelta(hours=3), "QZ20261121007"
        )
        flow.accept(accepted.id, now - timedelta(hours=2))
        flow.set_quote(accepted.id, 680)
        flow.confirm_payment(accepted.id, 680, now - timedelta(hours=1))
        flow.add_note(accepted.id, support, "用户已确认线下收款，约定次日上午当面交付。", now)
        accepted.program_name = "长街烟火"
        accepted.platform_label = "山东移动 · 专网 OTT"
        accepted.contact_name = "林秋"
        accepted.organization = "临江示例律所"
        accepted.requirement = "需要命中截图与时间戳，本周内交付。"

        def parked(number, user, status, program, platform, contact, created, **extra):
            row = EvidenceRequest(
                id=str(uuid4()),
                application_number=number,
                user_id=user.id,
                request_type="常规",
                status=status,
                created_at=created,
                program_name=program,
                platform_label=platform,
                contact_name=contact,
                organization=extra.get("organization"),
                requirement=extra.get("requirement"),
                quote_amount=extra.get("quote_amount"),
                payment_amount=extra.get("payment_amount"),
                payment_confirmed_at=extra.get("payment_confirmed_at"),
                accepted_at=extra.get("accepted_at"),
                delivery_method=extra.get("delivery_method"),
                delivery_note=extra.get("delivery_note"),
                delivered_at=extra.get("delivered_at"),
                failure_reason=extra.get("failure_reason"),
                refund_method=extra.get("refund_method"),
                refund_amount=extra.get("refund_amount"),
                failed_at=extra.get("failed_at"),
            )
            session.add(row)

        parked(
            "QZ20261119028",
            users["vip"],
            "待受理",
            "星河旧梦",
            "广东移动 · 公网",
            "赵闻",
            now - timedelta(days=4),
            requirement="超时未联系，列表应置顶。",
        )
        parked(
            "QZ20261120041",
            users["grant"],
            "已受理",
            "故人归途",
            "北京移动 · 公网",
            "许南",
            now - timedelta(days=1),
            quote_amount=420,
            accepted_at=now - timedelta(days=1),
        )
        parked(
            "QZ20261118012",
            users["paid"],
            "已交付",
            "海上生明月",
            "北京移动 · 公网",
            "何帆",
            now - timedelta(days=3),
            quote_amount=360,
            payment_amount=360,
            payment_confirmed_at=now - timedelta(days=2),
            accepted_at=now - timedelta(days=3),
            delivery_method="快递",
            delivery_note="经办人已当面核对单号",
            delivered_at=now - timedelta(days=1),
        )
        parked(
            "QZ20261117033",
            users["free"],
            "已失败",
            "竹影清风",
            "浙江联通 · 公网",
            "周安",
            now - timedelta(days=5),
            accepted_at=now - timedelta(days=5),
            failure_reason="用户取消，尚未收款",
            failed_at=now - timedelta(days=4),
        )
        parked(
            "QZ20261116008",
            users["member"],
            "已失败",
            "长街烟火",
            "江苏电信 · 专网",
            "林秋",
            now - timedelta(days=6),
            quote_amount=500,
            payment_amount=500,
            payment_confirmed_at=now - timedelta(days=5),
            accepted_at=now - timedelta(days=6),
            failure_reason="片源无法核验",
            refund_method="银行转账",
            refund_amount=500,
            failed_at=now - timedelta(days=4),
        )
        session.flush()

    @staticmethod
    def _content(session, now, audit: AuditWriter) -> None:
        SplashBook(session, audit, now).save(
            enabled=True,
            image_name="开屏示意.png",
            jump_label="首页示例「星河旧梦」",
            slogan="片单检索，免费查看较早的公开记录",
        )
        notes = AnnouncementBook(session, audit, now)
        notes.save(
            title="会员导出格式已更新",
            body="查询汇总现按一个平台命中一个节目逐行导出，内嵌水印截图。",
            button_text="我知道了",
            jump_label=None,
            starts_at=now - timedelta(days=1),
            ends_at=now + timedelta(days=14),
            frequency="once",
            online=True,
        )
        notes.save(
            title="取证交付说明",
            body="委托取证由客服线下交付，提交后 1 个工作日内联系。小程序不提供证据包下载。",
            button_text="查看说明",
            jump_label="取证申请页",
            starts_at=now - timedelta(days=2),
            ends_at=now + timedelta(days=40),
            frequency="once",
            online=True,
        )
        examples = ExampleBook(session, audit, now)
        for name in ("星河旧梦", "长街烟火", "南风知我意", "海上生明月"):
            examples.add(name, None)
        rows = examples.list_rows()
        if rows:
            examples.set_online(rows[-1]["id"], False)

    @staticmethod
    def _runs(session: Session, now) -> None:
        session.add(
            IndexRun(
                started_at=now - timedelta(days=3),
                finished_at=now - timedelta(days=3),
                new_rows=126,
                status="success",
                error_summary=None,
            )
        )
        session.add(
            IndexRun(
                started_at=now - timedelta(hours=3),
                finished_at=now - timedelta(hours=3),
                new_rows=0,
                status="failed",
                error_summary="读取队列状态表失败",
            )
        )
        session.add(
            IndexRun(
                started_at=now - timedelta(hours=1),
                finished_at=now - timedelta(hours=1),
                new_rows=0,
                status="failed",
                error_summary="读取队列状态表失败",
            )
        )
