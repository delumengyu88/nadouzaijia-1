"""Allow membership_dev on orders.product.

Revision ID: 20261122_0011
Revises: 20261122_0010
Create Date: 2026-11-22

Follows 20261122_0010 (WeChat Pay). Only ck_orders_product changes.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20261122_0011"
down_revision: str | None = "20261122_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_WITH_DEV = (
    "product IS NULL OR product IN "
    "('membership_regular', 'membership_vip', 'membership_test', 'membership_dev')"
)
_WITHOUT_DEV = (
    "product IS NULL OR product IN ('membership_regular', 'membership_vip', 'membership_test')"
)


def upgrade() -> None:
    op.drop_constraint("ck_orders_product", "orders", type_="check")
    op.create_check_constraint("ck_orders_product", "orders", _WITH_DEV)


def downgrade() -> None:
    op.drop_constraint("ck_orders_product", "orders", type_="check")
    op.create_check_constraint("ck_orders_product", "orders", _WITHOUT_DEV)
