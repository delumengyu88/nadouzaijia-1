"""Allow trial membership purchases alongside historical diamond orders."""

from alembic import op

revision = "20261123_0021"
down_revision = "20261123_0020"
branch_labels = None
depends_on = None

_BEFORE = (
    "'membership_regular', 'membership_vip', 'membership_test', "
    "'membership_dev', 'membership_test_live', 'membership_diamond'"
)


def _replace(codes: str) -> None:
    op.drop_constraint("ck_orders_product", "orders", type_="check")
    op.create_check_constraint(
        "ck_orders_product", "orders", f"product IS NULL OR product IN ({codes})"
    )


def upgrade() -> None:
    _replace(_BEFORE + ", 'membership_trial'")


def downgrade() -> None:
    # PostgreSQL rejects the whole transaction if a trial order exists.
    _replace(_BEFORE)
