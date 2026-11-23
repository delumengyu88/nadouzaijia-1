"""One-program band unlocks: 90–180天 and 30–90天, bought once and kept.

Adds orders.program_name, the band entitlement kinds, the band order tiers and
the two unlock product codes. 'membership_trial' is also allowed on orders so
this constraint already matches the pending trial-membership rename.
"""

import sqlalchemy as sa
from alembic import op

revision = "20261123_0022"
down_revision = "20261123_0020"
branch_labels = None
depends_on = None

_PRODUCTS_BEFORE = (
    "'membership_regular', 'membership_vip', 'membership_test', "
    "'membership_dev', 'membership_test_live', 'membership_diamond'"
)
_PRODUCTS_AFTER = (
    _PRODUCTS_BEFORE + ", 'membership_trial', 'unlock_band_90_180', 'unlock_band_30_90'"
)
_KINDS_BEFORE = "'regular', 'vip', 'program_90', 'program_30', 'screenshot'"
_KINDS_AFTER = _KINDS_BEFORE + ", 'band_90_180', 'band_30_90'"
_TIERS_BEFORE = "'regular', 'vip'"
_TIERS_AFTER = _TIERS_BEFORE + ", 'band_90_180', 'band_30_90'"


def _check(table: str, name: str, column: str, values: str, *, nullable: bool) -> None:
    op.drop_constraint(name, table, type_="check")
    clause = f"{column} IN ({values})"
    if nullable:
        clause = f"{column} IS NULL OR {clause}"
    op.create_check_constraint(name, table, clause)


def upgrade() -> None:
    op.add_column("orders", sa.Column("program_name", sa.String(200), nullable=True))
    _check("orders", "ck_orders_product", "product", _PRODUCTS_AFTER, nullable=True)
    _check("orders", "ck_orders_tier", "tier", _TIERS_AFTER, nullable=False)
    _check("entitlements", "ck_entitlements_kind", "kind", _KINDS_AFTER, nullable=False)


def downgrade() -> None:
    # PostgreSQL rejects the downgrade while any band order or band entitlement exists.
    # Remove or archive those rows first; they are paid purchases.
    _check("entitlements", "ck_entitlements_kind", "kind", _KINDS_BEFORE, nullable=False)
    _check("orders", "ck_orders_tier", "tier", _TIERS_BEFORE, nullable=False)
    _check("orders", "ck_orders_product", "product", _PRODUCTS_BEFORE, nullable=True)
    op.drop_column("orders", "program_name")
