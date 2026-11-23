"""Track authorized hit-list provenance and distinguish unverified search results."""

import sqlalchemy as sa
from alembic import op

revision = "20261123_0016"
down_revision = "20261122_0015"
branch_labels = None
depends_on = None

_BEFORE = "'exact', 'alias', 'manual', 'miss', 'pending', 'failed'"


def _resolution(codes: str) -> None:
    op.drop_constraint("ck_query_program_resolution", "query_programs", type_="check")
    op.create_check_constraint(
        "ck_query_program_resolution", "query_programs", f"resolution IN ({codes})"
    )


def upgrade() -> None:
    op.add_column("tasks", sa.Column("approved_hitlist_sha256", sa.String(64), nullable=True))
    op.add_column("index_records", sa.Column("hitlist_sha256", sa.String(64), nullable=True))
    _resolution(_BEFORE + ", 'unverified'")


def downgrade() -> None:
    # Keep historical provenance and unverified results intact when a downgrade
    # would lose information. PostgreSQL applies this revision transactionally.
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM tasks WHERE approved_hitlist_sha256 IS NOT NULL) "
        "OR EXISTS (SELECT 1 FROM index_records WHERE hitlist_sha256 IS NOT NULL) "
        "THEN RAISE EXCEPTION '已有来源记录，请保留当前迁移版本'; END IF; END $$"
    )
    _resolution(_BEFORE)
    op.drop_column("index_records", "hitlist_sha256")
    op.drop_column("tasks", "approved_hitlist_sha256")
