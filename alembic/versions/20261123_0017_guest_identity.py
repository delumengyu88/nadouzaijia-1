"""Keep locally issued guest identities separate from every existing account."""

import sqlalchemy as sa
from alembic import op

revision = "20261123_0017"
down_revision = "20261123_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users", sa.Column("is_guest", sa.Boolean(), nullable=False, server_default=sa.false())
    )


def downgrade() -> None:
    # Dropping the marker with live guest accounts would give their sessions account capabilities.
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM users WHERE is_guest) "
        "THEN RAISE EXCEPTION '已有游客身份，请保留当前迁移版本'; END IF; END $$"
    )
    op.drop_column("users", "is_guest")
