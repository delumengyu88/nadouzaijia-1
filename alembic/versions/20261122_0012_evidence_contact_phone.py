"""Add a nullable contact phone on evidence requests.

Revision ID: 20261122_0012
Revises: 20261122_0011
Create Date: 2026-11-22

Only adds evidence_requests.contact_phone. Existing rows keep every other
column; the new value is NULL until a user submits a request.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261122_0012"
down_revision: str | None = "20261122_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "evidence_requests",
        sa.Column("contact_phone", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("evidence_requests", "contact_phone")
