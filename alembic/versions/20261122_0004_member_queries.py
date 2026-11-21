"""member query tables for workbook export

Revision ID: 20261122_0004
Revises: 20261122_0003
Create Date: 2026-11-21

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261122_0004"
down_revision: str | None = "20261122_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "member_queries",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "query_programs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "query_id",
            sa.String(length=64),
            sa.ForeignKey("member_queries.id"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("uploaded_name", sa.String(length=200), nullable=False),
        sa.Column("program_type", sa.String(length=64), nullable=True),
        sa.Column("matched_name", sa.String(length=200), nullable=True),
        sa.Column("resolution", sa.String(length=16), nullable=False),
        sa.Column("detail", sa.String(length=1000), nullable=True),
        sa.CheckConstraint(
            "resolution IN ('exact', 'alias', 'manual', 'miss', 'pending', 'failed')",
            name="ck_query_program_resolution",
        ),
        sa.UniqueConstraint("query_id", "position", name="uq_query_program_position"),
    )
    op.create_index("ix_query_programs_query_id", "query_programs", ["query_id"])


def downgrade() -> None:
    op.drop_index("ix_query_programs_query_id", table_name="query_programs")
    op.drop_table("query_programs")
    op.drop_table("member_queries")
