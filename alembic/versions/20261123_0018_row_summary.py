"""Bind a display-only approval to a single existing index row."""

import sqlalchemy as sa
from alembic import op

revision = "20261123_0018"
down_revision = "20261123_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("index_records", sa.Column("summary_grant", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM index_records WHERE summary_grant IS NOT NULL) "
        "THEN RAISE EXCEPTION '已有单条展示授权'; END IF; END $$"
    )
    op.drop_column("index_records", "summary_grant")
