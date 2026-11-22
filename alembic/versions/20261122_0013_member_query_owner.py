"""Add the query owner and a user/time index for later retention deletes.

Revision ID: 20261122_0013
Revises: 20261122_0011
Create Date: 2026-11-22

Adds nullable member_queries.user_id (foreign key users.id) and nullable
seen_at. Index ix_member_queries_user_id_created_at is (user_id, created_at)
so a later job can delete one user's rows older than a cutoff. This revision
does not delete anything.

down_revision is temporarily 20261122_0011 so this branch upgrades on
acceptance before 20261122_0012 is merged. After that revision is on
acceptance, change down_revision to 20261122_0012. Do not add another 0012
in this branch.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261122_0013"
down_revision: str | None = "20261122_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "member_queries",
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", name="fk_member_queries_user_id"),
            nullable=True,
        ),
    )
    op.add_column(
        "member_queries",
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_member_queries_user_id_created_at",
        "member_queries",
        ["user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_member_queries_user_id_created_at", table_name="member_queries")
    op.drop_constraint("fk_member_queries_user_id", "member_queries", type_="foreignkey")
    op.drop_column("member_queries", "seen_at")
    op.drop_column("member_queries", "user_id")
