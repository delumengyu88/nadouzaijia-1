"""Store the WeChat Pay charge and transaction on an order.

Revision ID: 20261122_0010
Revises: 20261122_0009
Create Date: 2026-11-22

Follows 20261122_0009 (generated record dates). amount stays yuan for the
workbench. amount_fen is what WeChat Pay reports, in 分.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261122_0010"
down_revision: str | None = "20261122_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("product", sa.String(length=32), nullable=True))
    op.add_column("orders", sa.Column("amount_fen", sa.Integer(), nullable=True))
    op.add_column("orders", sa.Column("prepay_id", sa.String(length=64), nullable=True))
    op.add_column(
        "orders",
        sa.Column("wechat_transaction_id", sa.String(length=64), nullable=True),
    )
    op.create_unique_constraint(
        "uq_orders_wechat_transaction_id", "orders", ["wechat_transaction_id"]
    )
    op.create_check_constraint(
        "ck_orders_amount_fen",
        "orders",
        "amount_fen IS NULL OR amount_fen >= 0",
    )
    op.create_check_constraint(
        "ck_orders_product",
        "orders",
        "product IS NULL OR product IN ('membership_regular', 'membership_vip', 'membership_test')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_orders_product", "orders", type_="check")
    op.drop_constraint("ck_orders_amount_fen", "orders", type_="check")
    op.drop_constraint("uq_orders_wechat_transaction_id", "orders", type_="unique")
    op.drop_column("orders", "wechat_transaction_id")
    op.drop_column("orders", "prepay_id")
    op.drop_column("orders", "amount_fen")
    op.drop_column("orders", "product")
