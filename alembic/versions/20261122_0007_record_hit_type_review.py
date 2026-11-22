"""Quarantine configured program titles for review.

Revision ID: 20261122_0007
Revises: 20261122_0006
Create Date: 2026-11-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261122_0007"
down_revision: str | None = "20261122_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFAULTS = ("家庭", "远方", "狗")


def upgrade() -> None:
    titles = op.create_table(
        "review_program_titles",
        sa.Column("program_name", sa.String(length=200), primary_key=True, nullable=False),
    )
    op.bulk_insert(titles, [{"program_name": name} for name in _DEFAULTS])
    with op.batch_alter_table("index_records") as batch:
        batch.drop_constraint("ck_index_hit_type", type_="check")
        batch.create_check_constraint(
            "ck_index_hit_type", "hit_type IN ('consensus', 'dispute', 'review')"
        )
    # Index program_name is already canonicalized by IndexRecordStore.
    records = sa.table("index_records", sa.column("program_name"), sa.column("hit_type"))
    op.execute(
        records.update()
        .where(records.c.program_name.in_(_DEFAULTS), records.c.hit_type == "consensus")
        .values(hit_type="review")
    )


def downgrade() -> None:
    # Preserve hidden records on rollback; never turn an unreviewed hit into a billable hit.
    op.execute("UPDATE index_records SET hit_type = 'dispute' WHERE hit_type = 'review'")
    with op.batch_alter_table("index_records") as batch:
        batch.drop_constraint("ck_index_hit_type", type_="check")
        batch.create_check_constraint("ck_index_hit_type", "hit_type IN ('consensus', 'dispute')")
    op.drop_table("review_program_titles")
