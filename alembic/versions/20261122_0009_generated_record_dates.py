"""Store generated placeholder dates apart from real detection times.

Revision ID: 20261122_0009
Revises: 20261122_0008
Create Date: 2026-11-22

`uploaded_at` keeps the real time (or NULL). `generated_uploaded_at` holds a
generated placeholder and `date_generated` marks the row, so exports and
evidence prefill can exclude it and the values can be cleared in one pass.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261122_0009"
down_revision: str | None = "20261122_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("index_records") as batch:
        batch.add_column(sa.Column("generated_uploaded_at", sa.DateTime(timezone=True)))
        batch.add_column(
            sa.Column(
                "date_generated",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.create_check_constraint(
            "ck_index_generated_date",
            "(date_generated AND generated_uploaded_at IS NOT NULL AND uploaded_at IS NULL)"
            " OR (NOT date_generated AND generated_uploaded_at IS NULL)",
        )


def downgrade() -> None:
    with op.batch_alter_table("index_records") as batch:
        batch.drop_constraint("ck_index_generated_date", type_="check")
        batch.drop_column("date_generated")
        batch.drop_column("generated_uploaded_at")
