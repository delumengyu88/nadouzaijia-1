"""store platform province, operator, and subtype

Revision ID: 20261122_0002
Revises: 20261121_0003
Create Date: 2026-11-22

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261122_0002"
down_revision: str | None = "20261121_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("platform_province", sa.String(length=32), nullable=True))
    op.add_column("tasks", sa.Column("platform_operator", sa.String(length=64), nullable=True))
    op.add_column("tasks", sa.Column("platform_subtype", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "platform_subtype")
    op.drop_column("tasks", "platform_operator")
    op.drop_column("tasks", "platform_province")
