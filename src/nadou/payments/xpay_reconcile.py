"""Backstop: ask WeChat about virtual orders still 待支付 and grant the paid ones.

The client /sync call and the WeChat delivery push can both be lost (app closed,
API down). WeChat keeps the order paid, so this re-queries it and goes through
the same idempotent ``settle`` path as /sync. Dry run by default.
"""

import argparse
import logging
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from nadou.admin.notify import NotifierFactory
from nadou.admin.orders import OrderGrantWriter
from nadou.config import get_settings
from nadou.db import Database
from nadou.models import Order, User, VirtualOrder
from nadou.payments import xpay
from nadou.payments.xpay_api import PAID_STATUSES, settle

logger = logging.getLogger("nadou.payments")


def pending_orders(session, now, min_age, max_age):
    return (
        session.execute(
            select(Order.order_number)
            .join(VirtualOrder, VirtualOrder.order_id == Order.id)
            .where(
                Order.status == "待支付",
                Order.order_number.like("XV%"),
                Order.created_at <= now - min_age,
                Order.created_at >= now - max_age,
            )
            .order_by(Order.created_at)
        )
        .scalars()
        .all()
    )


def reconcile(
    database: Database,
    now: datetime,
    *,
    apply: bool,
    notifier=None,
    writer=None,
    min_age: timedelta = timedelta(minutes=2),
    max_age: timedelta = timedelta(days=3),
) -> dict:
    notifier = notifier or NotifierFactory.from_settings(get_settings())
    writer = writer or OrderGrantWriter()
    stats = {"checked": 0, "paid": 0, "granted": 0, "errors": 0}
    with database.session() as session:
        numbers = pending_orders(session, now, min_age, max_age)
    for number in numbers:
        stats["checked"] += 1
        with database.session() as session:
            try:
                order, virtual = session.execute(
                    select(Order, VirtualOrder)
                    .join(VirtualOrder, VirtualOrder.order_id == Order.id)
                    .where(Order.order_number == number)
                ).one()
                owner = session.get(User, order.user_id)
                if owner is None or not owner.wechat_openid:
                    continue
                session.commit()  # no transaction stays open across the WeChat call
                remote = xpay.query_order(virtual.env, owner.wechat_openid, number)
                if remote.get("status") not in PAID_STATUSES:
                    continue
                stats["paid"] += 1
                if not apply:
                    continue
                settle(session, order, virtual, remote, now, notifier, writer)
                session.commit()
                if order.status == "已发放":
                    stats["granted"] += 1
                    logger.info("xpay reconcile granted order=%s", number)
            except Exception as exc:  # one bad order must not stop the rest
                session.rollback()
                stats["errors"] += 1
                detail = getattr(exc, "detail", exc.__class__.__name__)
                logger.warning("xpay reconcile failed order=%s: %s", number, detail)
    return stats


def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m nadou xpay-reconcile")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="only report paid orders (default)")
    mode.add_argument("--apply", action="store_true", help="grant paid orders")
    parser.add_argument("--every", type=int, default=0, help="repeat every N seconds")
    args = parser.parse_args(argv)
    db = Database(get_settings().database_url)
    try:
        while True:
            stats = reconcile(db, datetime.now(UTC), apply=args.apply)
            print("xpay reconcile " + " ".join(f"{k}={v}" for k, v in stats.items()), flush=True)
            if args.every <= 0:
                return 0
            time.sleep(args.every)
    finally:
        db.engine.dispose()
