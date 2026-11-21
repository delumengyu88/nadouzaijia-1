"""initial schema

Revision ID: 20261121_0001
Revises:
Create Date: 2026-11-21

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261121_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "entitlements",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("program_name", sa.String(length=200), nullable=True),
        sa.Column("record_id", sa.String(length=36), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('regular', 'vip', 'program_90', 'program_30', 'screenshot')",
            name="ck_entitlements_kind",
        ),
    )
    op.create_index("ix_entitlements_user_id", "entitlements", ["user_id"])
    op.create_table(
        "tasks",
        sa.Column("task_id", sa.String(length=64), primary_key=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("file_uploaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_filename", sa.String(length=500), nullable=False),
        sa.Column("network_type", sa.String(length=8), nullable=True),
        sa.Column("platform_name", sa.String(length=200), nullable=True),
        sa.Column("source_unverified", sa.Boolean(), nullable=False),
        sa.Column("is_test", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            "status IN ('done', 'cancelled', 'duplicate')",
            name="ck_tasks_status",
        ),
        sa.CheckConstraint(
            "network_type IS NULL OR network_type IN ('公网', '专网')",
            name="ck_tasks_network",
        ),
    )
    op.create_table(
        "index_records",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("task_id", sa.String(length=64), nullable=False),
        sa.Column("program_name", sa.String(length=200), nullable=False),
        sa.Column("program_name_raw", sa.String(length=200), nullable=False),
        sa.Column("hit_type", sa.String(length=16), nullable=False),
        sa.Column("frame_count", sa.Integer(), nullable=False),
        sa.Column("timestamps", sa.JSON(), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "hit_type IN ('consensus', 'dispute')",
            name="ck_index_hit_type",
        ),
        sa.UniqueConstraint("task_id", "program_name", name="uq_index_task_program"),
    )
    op.create_index("ix_index_records_task_id", "index_records", ["task_id"])
    op.create_index("ix_index_records_program_name", "index_records", ["program_name"])
    op.create_table(
        "platform_aliases",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("alias", sa.String(length=200), nullable=False, unique=True),
        sa.Column("canonical_name", sa.String(length=200), nullable=False),
    )
    op.create_table(
        "program_aliases",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("alias", sa.String(length=200), nullable=False, unique=True),
        sa.Column("canonical_name", sa.String(length=200), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("program_aliases")
    op.drop_table("platform_aliases")
    op.drop_index("ix_index_records_program_name", table_name="index_records")
    op.drop_index("ix_index_records_task_id", table_name="index_records")
    op.drop_table("index_records")
    op.drop_table("tasks")
    op.drop_index("ix_entitlements_user_id", table_name="entitlements")
    op.drop_table("entitlements")
    op.drop_table("users")
