"""Keep virtual checkout metadata separate from existing orders."""

import sqlalchemy as sa
from alembic import op

revision = "20261122_0014"
down_revision = "20261122_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "virtual_orders",
        sa.Column("order_id", sa.String(36), sa.ForeignKey("orders.id"), primary_key=True),
        sa.Column("env", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.String(128), nullable=False),
        sa.Column("delivery_confirmed", sa.Boolean(), nullable=False),
        sa.Column("refund_no", sa.String(64), unique=True),
        sa.Column("wx_refund_no", sa.String(64), unique=True),
    )


def downgrade() -> None:
    op.drop_table("virtual_orders")
