"""evidence request states for the workbench and the mini program

Revision ID: 20261122_0005
Revises: 20261122_0004
Create Date: 2026-11-22

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261122_0005"
down_revision: str | None = "20261122_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATUSES = (
    "'已提交', '已受理', '待付款（线下）', '取证中', '已交付', "
    "'已完成', '取证失败', '已关闭', '已取消'"
)


def upgrade() -> None:
    op.add_column("evidence_requests", sa.Column("quoted_at", sa.DateTime(timezone=True)))
    op.add_column("evidence_requests", sa.Column("completed_at", sa.DateTime(timezone=True)))
    op.add_column("evidence_requests", sa.Column("closed_at", sa.DateTime(timezone=True)))
    op.add_column("evidence_requests", sa.Column("cancelled_at", sa.DateTime(timezone=True)))
    op.drop_constraint("ck_evidence_status", "evidence_requests", type_="check")
    op.execute("UPDATE evidence_requests SET status = '已提交' WHERE status = '待受理'")
    op.execute("UPDATE evidence_requests SET status = '取证失败' WHERE status = '已失败'")
    op.create_check_constraint(
        "ck_evidence_status",
        "evidence_requests",
        f"status IN ({_STATUSES})",
    )


def downgrade() -> None:
    op.drop_constraint("ck_evidence_status", "evidence_requests", type_="check")
    op.execute("UPDATE evidence_requests SET status = '待受理' WHERE status = '已提交'")
    op.execute("UPDATE evidence_requests SET status = '已失败' WHERE status = '取证失败'")
    op.execute(
        "UPDATE evidence_requests SET status = '已受理' "
        "WHERE status IN ('待付款（线下）', '已完成', '已关闭', '已取消')"
    )
    op.create_check_constraint(
        "ck_evidence_status",
        "evidence_requests",
        "status IN ('待受理', '已受理', '取证中', '已交付', '已失败')",
    )
    op.drop_column("evidence_requests", "cancelled_at")
    op.drop_column("evidence_requests", "closed_at")
    op.drop_column("evidence_requests", "completed_at")
    op.drop_column("evidence_requests", "quoted_at")
