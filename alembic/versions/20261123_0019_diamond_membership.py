"""Allow the formal diamond membership product on orders."""

from alembic import op

revision = "20261123_0019"
down_revision = "20261123_0018"
branch_labels = None
depends_on = None

_BEFORE = (
    "'membership_regular', 'membership_vip', 'membership_test', "
    "'membership_dev', 'membership_test_live'"
)


def _replace(codes: str) -> None:
    op.drop_constraint("ck_orders_product", "orders", type_="check")
    op.create_check_constraint(
        "ck_orders_product", "orders", f"product IS NULL OR product IN ({codes})"
    )


def upgrade() -> None:
    _replace(_BEFORE + ", 'membership_diamond'")


def downgrade() -> None:
    # PostgreSQL rejects the whole transaction if a diamond order exists.
    _replace(_BEFORE)
