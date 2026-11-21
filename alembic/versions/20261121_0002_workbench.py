"""workbench schema

Revision ID: 20261121_0002
Revises: 20261121_0001
Create Date: 2026-11-21

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261121_0002"
down_revision: str | None = "20261121_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BAN_UNTIL = (
    "((ban_type IS NULL AND ban_until IS NULL) OR "
    "(ban_type = 'permanent' AND ban_until IS NULL) OR "
    "(ban_type = 'temporary' AND ban_until IS NOT NULL))"
)


def upgrade() -> None:
    op.add_column("users", sa.Column("phone", sa.String(length=32), nullable=True))
    op.add_column("users", sa.Column("ban_type", sa.String(length=16), nullable=True))
    op.add_column("users", sa.Column("ban_reason", sa.String(length=500), nullable=True))
    op.add_column("users", sa.Column("ban_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("banned_at", sa.DateTime(timezone=True), nullable=True))
    op.create_unique_constraint("users_phone_key", "users", ["phone"])
    op.create_check_constraint(
        "ck_users_ban_type",
        "users",
        "ban_type IS NULL OR ban_type IN ('temporary', 'permanent')",
    )
    op.create_check_constraint("ck_users_ban_until", "users", _BAN_UNTIL)

    op.add_column("entitlements", sa.Column("source", sa.String(length=16), nullable=True))
    op.create_check_constraint(
        "ck_entitlements_source",
        "entitlements",
        "source IS NULL OR source IN ('manual', 'order')",
    )

    op.create_table(
        "staff_accounts",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("username", sa.String(length=64), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("role IN ('support', 'admin')", name="ck_staff_role"),
    )
    op.create_table(
        "staff_login_locks",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "account_id",
            sa.String(length=36),
            sa.ForeignKey("staff_accounts.id"),
            nullable=False,
        ),
        sa.Column("ip", sa.String(length=64), nullable=False),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("account_id", "ip", name="uq_staff_lock_account_ip"),
    )
    op.create_table(
        "staff_sessions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column(
            "account_id",
            sa.String(length=36),
            sa.ForeignKey("staff_accounts.id"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_staff_sessions_account_id", "staff_sessions", ["account_id"])
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("actor_id", sa.String(length=36), nullable=True),
        sa.Column("actor_username", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("object_type", sa.String(length=64), nullable=False),
        sa.Column("object_id", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("before_json", sa.JSON(), nullable=True),
        sa.Column("after_json", sa.JSON(), nullable=True),
        sa.Column("ip", sa.String(length=64), nullable=True),
    )
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])
    op.create_table(
        "evidence_requests",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("application_number", sa.String(length=32), nullable=False, unique=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("request_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("quote_amount", sa.Integer(), nullable=True),
        sa.Column("payment_amount", sa.Integer(), nullable=True),
        sa.Column("payment_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivery_method", sa.String(length=16), nullable=True),
        sa.Column("delivery_note", sa.String(length=500), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_reason", sa.String(length=500), nullable=True),
        sa.Column("refund_method", sa.String(length=32), nullable=True),
        sa.Column("refund_amount", sa.Integer(), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("overdue_notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('待受理', '已受理', '取证中', '已交付', '已失败')",
            name="ck_evidence_status",
        ),
        sa.CheckConstraint(
            "delivery_method IS NULL OR delivery_method IN ('当面', '快递', '企业网盘', '其他')",
            name="ck_evidence_delivery",
        ),
    )
    op.create_index("ix_evidence_requests_user_id", "evidence_requests", ["user_id"])
    op.create_index("ix_evidence_requests_created_at", "evidence_requests", ["created_at"])
    op.create_table(
        "evidence_notes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "request_id",
            sa.String(length=36),
            sa.ForeignKey("evidence_requests.id"),
            nullable=False,
        ),
        sa.Column(
            "author_id",
            sa.String(length=36),
            sa.ForeignKey("staff_accounts.id"),
            nullable=False,
        ),
        sa.Column("author_username", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("body", sa.String(length=2000), nullable=False),
    )
    op.create_index("ix_evidence_notes_request_id", "evidence_notes", ["request_id"])
    op.create_table(
        "orders",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("order_number", sa.String(length=32), nullable=False, unique=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("tier", sa.String(length=16), nullable=False),
        sa.Column("duration_days", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('待支付', '已支付', '已发放', '发放异常')",
            name="ck_orders_status",
        ),
        sa.CheckConstraint("tier IN ('regular', 'vip')", name="ck_orders_tier"),
        sa.CheckConstraint("duration_days > 0", name="ck_orders_duration"),
        sa.CheckConstraint("amount >= 0", name="ck_orders_amount"),
    )
    op.create_index("ix_orders_user_id", "orders", ["user_id"])
    op.create_table(
        "order_issuances",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("order_id", sa.String(length=36), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("tier", sa.String(length=16), nullable=False),
        sa.Column("duration_days", sa.Integer(), nullable=False),
        sa.Column("entitlement_id", sa.Integer(), sa.ForeignKey("entitlements.id"), nullable=True),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("reissued", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("order_id", name="uq_order_issuance_order"),
        sa.CheckConstraint(
            "status IN ('pending', 'issued', '发放异常')",
            name="ck_issuance_status",
        ),
    )
    op.create_table(
        "index_runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("new_rows", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error_summary", sa.String(length=500), nullable=True),
        sa.CheckConstraint(
            "status IN ('running', 'success', 'failed')",
            name="ck_index_run_status",
        ),
    )
    op.create_table(
        "index_run_locks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "index_alert_episodes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
    )
    _append_only_guards()


def _append_only_guards() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            "CREATE OR REPLACE FUNCTION nadou_reject_mutation() RETURNS trigger "
            "LANGUAGE plpgsql AS $$ BEGIN "
            "RAISE EXCEPTION '% is append-only', TG_TABLE_NAME; "
            "END; $$;"
        )
        for table in ("audit_logs", "evidence_notes"):
            op.execute(
                f"CREATE TRIGGER {table}_no_update BEFORE UPDATE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION nadou_reject_mutation()"
            )
            op.execute(
                f"CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION nadou_reject_mutation()"
            )
        return
    if bind.dialect.name != "sqlite":
        return
    for table in ("audit_logs", "evidence_notes"):
        op.execute(
            f"CREATE TRIGGER {table}_no_update BEFORE UPDATE ON {table} "
            f"BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END"
        )
        op.execute(
            f"CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table} "
            f"BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END"
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for table in ("evidence_notes", "audit_logs"):
            op.execute(f"DROP TRIGGER IF EXISTS {table}_no_update ON {table}")
            op.execute(f"DROP TRIGGER IF EXISTS {table}_no_delete ON {table}")
        op.execute("DROP FUNCTION IF EXISTS nadou_reject_mutation()")
    elif bind.dialect.name == "sqlite":
        for table in ("evidence_notes", "audit_logs"):
            op.execute(f"DROP TRIGGER IF EXISTS {table}_no_update")
            op.execute(f"DROP TRIGGER IF EXISTS {table}_no_delete")

    op.drop_table("index_alert_episodes")
    op.drop_table("index_run_locks")
    op.drop_table("index_runs")
    op.drop_table("order_issuances")
    op.drop_index("ix_orders_user_id", table_name="orders")
    op.drop_table("orders")
    op.drop_index("ix_evidence_notes_request_id", table_name="evidence_notes")
    op.drop_table("evidence_notes")
    op.drop_index("ix_evidence_requests_created_at", table_name="evidence_requests")
    op.drop_index("ix_evidence_requests_user_id", table_name="evidence_requests")
    op.drop_table("evidence_requests")
    op.drop_index("ix_audit_logs_created_at", table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_index("ix_staff_sessions_account_id", table_name="staff_sessions")
    op.drop_table("staff_sessions")
    op.drop_table("staff_login_locks")
    op.drop_table("staff_accounts")

    op.drop_constraint("ck_entitlements_source", "entitlements", type_="check")
    op.drop_column("entitlements", "source")

    op.drop_constraint("ck_users_ban_until", "users", type_="check")
    op.drop_constraint("ck_users_ban_type", "users", type_="check")
    op.drop_constraint("users_phone_key", "users", type_="unique")
    op.drop_column("users", "banned_at")
    op.drop_column("users", "ban_until")
    op.drop_column("users", "ban_reason")
    op.drop_column("users", "ban_type")
    op.drop_column("users", "phone")
