"""store the offline refund note on an evidence request

Revision ID: 20261122_0006
Revises: 20261122_0005
Create Date: 2026-11-22

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261122_0006"
down_revision: str | None = "20261122_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("evidence_requests", sa.Column("refund_note", sa.String(length=200)))


def downgrade() -> None:
    op.drop_column("evidence_requests", "refund_note")
