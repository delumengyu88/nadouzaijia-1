"""store optional 类别 from the old consensus sheet

Revision ID: 20261122_0003
Revises: 20261122_0002
Create Date: 2026-11-22

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261122_0003"
down_revision: str | None = "20261122_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("index_records", sa.Column("category", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("index_records", "category")
