"""Allow the live-only one-yuan test product on orders."""

from alembic import op

revision = "20261122_0015"
down_revision = "20261122_0014"
branch_labels = None
depends_on = None

_BEFORE = "'membership_regular', 'membership_vip', 'membership_test', 'membership_dev'"


def _replace(codes: str) -> None:
    op.drop_constraint("ck_orders_product", "orders", type_="check")
    op.create_check_constraint(
        "ck_orders_product", "orders", f"product IS NULL OR product IN ({codes})"
    )


def upgrade() -> None:
    _replace(_BEFORE + ", 'membership_test_live'")


def downgrade() -> None:
    # PostgreSQL validates the narrower constraint in the same transaction. If a
    # live test order exists the whole downgrade rolls back, preserving every order.
    _replace(_BEFORE)
