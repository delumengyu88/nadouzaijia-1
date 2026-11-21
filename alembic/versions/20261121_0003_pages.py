"""workbench pages: invites, content, contact fields, refunds

Revision ID: 20261121_0003
Revises: 20261121_0002
Create Date: 2026-11-21

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261121_0003"
down_revision: str | None = "20261121_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("wechat_openid", sa.String(length=64), nullable=True))
    op.add_column("users", sa.Column("display_name", sa.String(length=64), nullable=True))
    op.create_unique_constraint("users_wechat_openid_key", "users", ["wechat_openid"])

    for name, length in (
        ("program_name", 200),
        ("platform_label", 200),
        ("record_ref", 64),
        ("contact_name", 64),
        ("organization", 200),
        ("requirement", 500),
    ):
        op.add_column("evidence_requests", sa.Column(name, sa.String(length=length), nullable=True))

    op.drop_constraint("ck_entitlements_source", "entitlements", type_="check")
    op.create_check_constraint(
        "ck_entitlements_source",
        "entitlements",
        "source IS NULL OR source IN ('manual', 'order', 'invite')",
    )
    op.drop_constraint("ck_orders_status", "orders", type_="check")
    op.create_check_constraint(
        "ck_orders_status",
        "orders",
        "status IN ('待支付', '已支付', '已发放', '发放异常', '已退款')",
    )
    op.add_column("orders", sa.Column("client", sa.String(length=16), nullable=True))
    op.add_column("orders", sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("orders", sa.Column("refunded_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("orders", sa.Column("refund_reason", sa.String(length=500), nullable=True))

    op.create_table(
        "invite_codes",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("code", sa.String(length=8), nullable=False, unique=True),
        sa.Column("channel", sa.String(length=64), nullable=False),
        sa.Column(
            "created_by",
            sa.String(length=36),
            sa.ForeignKey("staff_accounts.id"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("redeemed_by", sa.String(length=36), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("redeemed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("voided_by", sa.String(length=64), nullable=True),
    )
    op.create_table(
        "splash_config",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("image_name", sa.String(length=200), nullable=False),
        sa.Column("jump_label", sa.String(length=200), nullable=True),
        sa.Column("slogan", sa.String(length=80), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "announcements",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("title", sa.String(length=80), nullable=False),
        sa.Column("body", sa.String(length=200), nullable=False),
        sa.Column("button_text", sa.String(length=32), nullable=False),
        sa.Column("jump_label", sa.String(length=200), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("frequency", sa.String(length=16), nullable=False),
        sa.Column("online", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("frequency IN ('once', 'every')", name="ck_announcement_frequency"),
    )
    op.create_table(
        "homepage_examples",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("program_name", sa.String(length=200), nullable=False),
        sa.Column("record_id", sa.String(length=36), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("online", sa.Boolean(), nullable=False),
        sa.Column("platform_hits", sa.Integer(), nullable=False),
        sa.Column("detected_on", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("homepage_examples")
    op.drop_table("announcements")
    op.drop_table("splash_config")
    op.drop_table("invite_codes")
    op.drop_column("orders", "refund_reason")
    op.drop_column("orders", "refunded_at")
    op.drop_column("orders", "paid_at")
    op.drop_column("orders", "client")
    op.drop_constraint("ck_orders_status", "orders", type_="check")
    op.create_check_constraint(
        "ck_orders_status",
        "orders",
        "status IN ('待支付', '已支付', '已发放', '发放异常')",
    )
    op.drop_constraint("ck_entitlements_source", "entitlements", type_="check")
    op.create_check_constraint(
        "ck_entitlements_source",
        "entitlements",
        "source IS NULL OR source IN ('manual', 'order')",
    )
    for name in (
        "requirement",
        "organization",
        "contact_name",
        "record_ref",
        "platform_label",
        "program_name",
    ):
        op.drop_column("evidence_requests", name)
    op.drop_constraint("users_wechat_openid_key", "users", type_="unique")
    op.drop_column("users", "display_name")
    op.drop_column("users", "wechat_openid")
