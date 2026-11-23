"""Record independent anonymous-publication rights without changing existing examples."""

import sqlalchemy as sa
from alembic import op

revision = "20261123_0020"
down_revision = "20261123_0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "public_example_permits",
        sa.Column("record_id", sa.String(36), primary_key=True),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("permission_sha256", sa.String(64), nullable=False),
        sa.Column("row_sha256", sa.String(64), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("publication_token", sa.String(36), nullable=False),
    )

    op.add_column("homepage_examples", sa.Column("publication_token", sa.String(36)))


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM public_example_permits) "
        "THEN RAISE EXCEPTION '已有匿名公开授权记录'; END IF; END $$"
    )
    op.drop_column("homepage_examples", "publication_token")
    op.drop_table("public_example_permits")
