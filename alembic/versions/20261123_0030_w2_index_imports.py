"""Record which W2 release is imported into index_records.

Follows 20261123_0022 (one-program band unlocks). Numbered 0030 so it sorts after
0021-0029 revisions added on other branches from the same 0020 base.
"""

import sqlalchemy as sa
from alembic import op

revision = "20261123_0030"
down_revision = "20261123_0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "w2_index_imports",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("db_sha256", sa.String(64), nullable=True),
        sa.Column("build_ts", sa.String(40), nullable=True),
        sa.Column("consensus_names", sa.Integer(), nullable=True),
        sa.Column("kept_consensus_names", sa.Integer(), nullable=True),
        sa.Column("hit_rows", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("stats", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("error_summary", sa.String(500), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('success', 'failed')", name="ck_w2_import_status"),
    )
    op.create_index("ix_w2_index_imports_version", "w2_index_imports", ["version"])
    op.create_index("ix_w2_index_imports_finished_at", "w2_index_imports", ["finished_at"])


def downgrade() -> None:
    # Only the import log is dropped. index_records and tasks keep the imported rows;
    # rerun import-approved-hitlist or restore a backup to change what users see.
    op.drop_index("ix_w2_index_imports_finished_at", table_name="w2_index_imports")
    op.drop_index("ix_w2_index_imports_version", table_name="w2_index_imports")
    op.drop_table("w2_index_imports")
