from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from nadou.db import AwareDateTime, Base


class AppendOnlyViolation(RuntimeError):
    """Raised when application code tries to change an append-only row."""


class AppendOnlyGuard:
    """Block ORM updates and install SQLite triggers for create_all tests.

    Postgres triggers are created by the Alembic migration. create_all does not
    run that migration, so the SQLite hook covers the same rule in unit tests.
    """

    @staticmethod
    def reject(mapper, connection, target) -> None:
        del mapper, connection
        raise AppendOnlyViolation(f"{target.__table__.name} is append-only")

    @staticmethod
    def sqlite_triggers(table_name: str):
        message = f"{table_name} is append-only"

        def _install(target, connection, **kwargs) -> None:
            del target, kwargs
            if connection.dialect.name != "sqlite":
                return
            connection.exec_driver_sql(
                f"CREATE TRIGGER IF NOT EXISTS {table_name}_no_update "
                f"BEFORE UPDATE ON {table_name} "
                f"BEGIN SELECT RAISE(ABORT, '{message}'); END"
            )
            connection.exec_driver_sql(
                f"CREATE TRIGGER IF NOT EXISTS {table_name}_no_delete "
                f"BEFORE DELETE ON {table_name} "
                f"BEGIN SELECT RAISE(ABORT, '{message}'); END"
            )

        return _install


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "ban_type IS NULL OR ban_type IN ('temporary', 'permanent')",
            name="ck_users_ban_type",
        ),
        CheckConstraint(
            "("
            "(ban_type IS NULL AND ban_until IS NULL) OR "
            "(ban_type = 'permanent' AND ban_until IS NULL) OR "
            "(ban_type = 'temporary' AND ban_until IS NOT NULL)"
            ")",
            name="ck_users_ban_until",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(AwareDateTime, nullable=False)
    phone: Mapped[str | None] = mapped_column(String(32), unique=True)
    wechat_openid: Mapped[str | None] = mapped_column(String(64), unique=True)
    display_name: Mapped[str | None] = mapped_column(String(64))
    ban_type: Mapped[str | None] = mapped_column(String(16))
    ban_reason: Mapped[str | None] = mapped_column(String(500))
    ban_until: Mapped[datetime | None] = mapped_column(AwareDateTime)
    banned_at: Mapped[datetime | None] = mapped_column(AwareDateTime)
    entitlements: Mapped[list["Entitlement"]] = relationship(back_populates="user")


class Entitlement(Base):
    __tablename__ = "entitlements"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('regular', 'vip', 'program_90', 'program_30', 'screenshot')",
            name="ck_entitlements_kind",
        ),
        CheckConstraint(
            "source IS NULL OR source IN ('manual', 'order', 'invite')",
            name="ck_entitlements_source",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    user: Mapped[User] = relationship(back_populates="entitlements")
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    program_name: Mapped[str | None] = mapped_column(String(200))
    record_id: Mapped[str | None] = mapped_column(String(36))
    expires_at: Mapped[datetime] = mapped_column(AwareDateTime, nullable=False)
    # manual grants are excluded from invite-code conversion and order revenue.
    source: Mapped[str | None] = mapped_column(String(16))


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint(
            "status IN ('done', 'cancelled', 'duplicate')",
            name="ck_tasks_status",
        ),
        CheckConstraint(
            "network_type IS NULL OR network_type IN ('公网', '专网')",
            name="ck_tasks_network",
        ),
    )

    task_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(AwareDateTime)
    file_uploaded_at: Mapped[datetime | None] = mapped_column(AwareDateTime)
    raw_filename: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    network_type: Mapped[str | None] = mapped_column(String(8))
    platform_name: Mapped[str | None] = mapped_column(String(200))
    platform_province: Mapped[str | None] = mapped_column(String(32))
    platform_operator: Mapped[str | None] = mapped_column(String(64))
    platform_subtype: Mapped[str | None] = mapped_column(String(32))
    source_unverified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_test: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class IndexRecord(Base):
    __tablename__ = "index_records"
    __table_args__ = (
        UniqueConstraint("task_id", "program_name", name="uq_index_task_program"),
        CheckConstraint(
            "hit_type IN ('consensus', 'dispute')",
            name="ck_index_hit_type",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    program_name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    program_name_raw: Mapped[str] = mapped_column(String(200), nullable=False)
    hit_type: Mapped[str] = mapped_column(String(16), nullable=False)
    category: Mapped[str | None] = mapped_column(String(64))
    frame_count: Mapped[int] = mapped_column(Integer, nullable=False)
    timestamps: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    uploaded_at: Mapped[datetime | None] = mapped_column(AwareDateTime)


class PlatformAlias(Base):
    __tablename__ = "platform_aliases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alias: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    canonical_name: Mapped[str] = mapped_column(String(200), nullable=False)


class ProgramAlias(Base):
    __tablename__ = "program_aliases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alias: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    canonical_name: Mapped[str] = mapped_column(String(200), nullable=False)


class StaffAccount(Base):
    __tablename__ = "staff_accounts"
    __table_args__ = (CheckConstraint("role IN ('support', 'admin')", name="ck_staff_role"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(AwareDateTime, nullable=False)


class StaffLoginLock(Base):
    """Consecutive failures for one staff account from one client address."""

    __tablename__ = "staff_login_locks"
    __table_args__ = (UniqueConstraint("account_id", "ip", name="uq_staff_lock_account_ip"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("staff_accounts.id"), nullable=False)
    ip: Mapped[str] = mapped_column(String(64), nullable=False)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(AwareDateTime)


class StaffSession(Base):
    __tablename__ = "staff_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    account_id: Mapped[str] = mapped_column(
        ForeignKey("staff_accounts.id"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(AwareDateTime, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(AwareDateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(AwareDateTime)


class AuditLog(Base):
    """Append-only workbench history. No update or delete API."""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_id: Mapped[str | None] = mapped_column(String(36))
    actor_username: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(AwareDateTime, nullable=False, index=True)
    object_type: Mapped[str] = mapped_column(String(64), nullable=False)
    object_id: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    before_json: Mapped[dict | None] = mapped_column(JSON)
    after_json: Mapped[dict | None] = mapped_column(JSON)
    ip: Mapped[str | None] = mapped_column(String(64))


class EvidenceRequest(Base):
    __tablename__ = "evidence_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN ("
            "'已提交', '已受理', '待付款（线下）', '取证中', '已交付', "
            "'已完成', '取证失败', '已关闭', '已取消'"
            ")",
            name="ck_evidence_status",
        ),
        CheckConstraint(
            "delivery_method IS NULL OR delivery_method IN ('当面', '快递', '企业网盘', '其他')",
            name="ck_evidence_delivery",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    application_number: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    user: Mapped[User] = relationship()
    request_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(AwareDateTime, nullable=False, index=True)
    accepted_at: Mapped[datetime | None] = mapped_column(AwareDateTime)
    quoted_at: Mapped[datetime | None] = mapped_column(AwareDateTime)
    quote_amount: Mapped[int | None] = mapped_column(Integer)
    payment_amount: Mapped[int | None] = mapped_column(Integer)
    payment_confirmed_at: Mapped[datetime | None] = mapped_column(AwareDateTime)
    delivery_method: Mapped[str | None] = mapped_column(String(16))
    delivery_note: Mapped[str | None] = mapped_column(String(500))
    delivered_at: Mapped[datetime | None] = mapped_column(AwareDateTime)
    completed_at: Mapped[datetime | None] = mapped_column(AwareDateTime)
    closed_at: Mapped[datetime | None] = mapped_column(AwareDateTime)
    cancelled_at: Mapped[datetime | None] = mapped_column(AwareDateTime)
    failure_reason: Mapped[str | None] = mapped_column(String(500))
    refund_method: Mapped[str | None] = mapped_column(String(32))
    refund_amount: Mapped[int | None] = mapped_column(Integer)
    failed_at: Mapped[datetime | None] = mapped_column(AwareDateTime)
    overdue_notified_at: Mapped[datetime | None] = mapped_column(AwareDateTime)
    program_name: Mapped[str | None] = mapped_column(String(200))
    platform_label: Mapped[str | None] = mapped_column(String(200))
    record_ref: Mapped[str | None] = mapped_column(String(64))
    contact_name: Mapped[str | None] = mapped_column(String(64))
    organization: Mapped[str | None] = mapped_column(String(200))
    requirement: Mapped[str | None] = mapped_column(String(500))
    notes: Mapped[list["EvidenceNote"]] = relationship(back_populates="request")


class EvidenceNote(Base):
    """Append-only 沟通备注. Staff can add a note; nobody can edit or delete one."""

    __tablename__ = "evidence_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(
        ForeignKey("evidence_requests.id"), nullable=False, index=True
    )
    request: Mapped[EvidenceRequest] = relationship(back_populates="notes")
    author_id: Mapped[str] = mapped_column(ForeignKey("staff_accounts.id"), nullable=False)
    author_username: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(AwareDateTime, nullable=False)
    body: Mapped[str] = mapped_column(String(2000), nullable=False)


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint(
            "status IN ('待支付', '已支付', '已发放', '发放异常', '已退款')",
            name="ck_orders_status",
        ),
        CheckConstraint("tier IN ('regular', 'vip')", name="ck_orders_tier"),
        CheckConstraint("duration_days > 0", name="ck_orders_duration"),
        CheckConstraint("amount >= 0", name="ck_orders_amount"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    order_number: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    user: Mapped[User] = relationship()
    tier: Mapped[str] = mapped_column(String(16), nullable=False)
    duration_days: Mapped[int] = mapped_column(Integer, nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(AwareDateTime, nullable=False)
    client: Mapped[str | None] = mapped_column(String(16))
    paid_at: Mapped[datetime | None] = mapped_column(AwareDateTime)
    refunded_at: Mapped[datetime | None] = mapped_column(AwareDateTime)
    refund_reason: Mapped[str | None] = mapped_column(String(500))


class OrderIssuance(Base):
    """One issuance attempt chain per order. The unique order id makes it idempotent."""

    __tablename__ = "order_issuances"
    __table_args__ = (
        UniqueConstraint("order_id", name="uq_order_issuance_order"),
        CheckConstraint(
            "status IN ('pending', 'issued', '发放异常')",
            name="ck_issuance_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tier: Mapped[str] = mapped_column(String(16), nullable=False)
    duration_days: Mapped[int] = mapped_column(Integer, nullable=False)
    entitlement_id: Mapped[int | None] = mapped_column(ForeignKey("entitlements.id"))
    last_error: Mapped[str | None] = mapped_column(String(500))
    reissued: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(AwareDateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(AwareDateTime, nullable=False)


class MemberQuery(Base):
    """One member lookup. The id is the 查询编号 on the exported workbook."""

    __tablename__ = "member_queries"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(AwareDateTime, nullable=False)
    programs: Mapped[list["QueryProgram"]] = relationship(
        back_populates="query",
        order_by="QueryProgram.position",
    )


class QueryProgram(Base):
    """One uploaded name inside a member query."""

    __tablename__ = "query_programs"
    __table_args__ = (
        UniqueConstraint("query_id", "position", name="uq_query_program_position"),
        CheckConstraint(
            "resolution IN ('exact', 'alias', 'manual', 'miss', 'pending', 'failed')",
            name="ck_query_program_resolution",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    query_id: Mapped[str] = mapped_column(
        ForeignKey("member_queries.id"),
        nullable=False,
        index=True,
    )
    query: Mapped[MemberQuery] = relationship(back_populates="programs")
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    uploaded_name: Mapped[str] = mapped_column(String(200), nullable=False)
    program_type: Mapped[str | None] = mapped_column(String(64))
    matched_name: Mapped[str | None] = mapped_column(String(200))
    resolution: Mapped[str] = mapped_column(String(16), nullable=False)
    detail: Mapped[str | None] = mapped_column(String(1000))


class IndexRun(Base):
    __tablename__ = "index_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'success', 'failed')",
            name="ck_index_run_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(AwareDateTime, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(AwareDateTime)
    new_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error_summary: Mapped[str | None] = mapped_column(String(500))


class IndexRunLock(Base):
    """Single-row lock used where Postgres advisory locks are not available."""

    __tablename__ = "index_run_locks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    acquired_at: Mapped[datetime] = mapped_column(AwareDateTime, nullable=False)


class IndexAlertEpisode(Base):
    """One open row means the staleness notifier has already fired for this gap."""

    __tablename__ = "index_alert_episodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    opened_at: Mapped[datetime] = mapped_column(AwareDateTime, nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(AwareDateTime)


class InviteCode(Base):
    __tablename__ = "invite_codes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    code: Mapped[str] = mapped_column(String(8), unique=True, nullable=False)
    channel: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[str] = mapped_column(ForeignKey("staff_accounts.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(AwareDateTime, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(AwareDateTime, nullable=False)
    redeemed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    redeemed_at: Mapped[datetime | None] = mapped_column(AwareDateTime)
    voided_at: Mapped[datetime | None] = mapped_column(AwareDateTime)
    voided_by: Mapped[str | None] = mapped_column(String(64))


class SplashConfig(Base):
    __tablename__ = "splash_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    image_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    jump_label: Mapped[str | None] = mapped_column(String(200))
    slogan: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(AwareDateTime, nullable=False)


class Announcement(Base):
    __tablename__ = "announcements"
    __table_args__ = (
        CheckConstraint("frequency IN ('once', 'every')", name="ck_announcement_frequency"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(80), nullable=False)
    body: Mapped[str] = mapped_column(String(200), nullable=False)
    button_text: Mapped[str] = mapped_column(String(32), nullable=False)
    jump_label: Mapped[str | None] = mapped_column(String(200))
    starts_at: Mapped[datetime] = mapped_column(AwareDateTime, nullable=False)
    ends_at: Mapped[datetime] = mapped_column(AwareDateTime, nullable=False)
    frequency: Mapped[str] = mapped_column(String(16), nullable=False)
    online: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(AwareDateTime, nullable=False)


class HomepageExample(Base):
    """At most five rows. The workbench enforces the cap."""

    __tablename__ = "homepage_examples"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    program_name: Mapped[str] = mapped_column(String(200), nullable=False)
    record_id: Mapped[str | None] = mapped_column(String(36))
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    online: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    platform_hits: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    detected_on: Mapped[datetime | None] = mapped_column(AwareDateTime)


for _append_only in (AuditLog, EvidenceNote):
    event.listen(_append_only, "before_update", AppendOnlyGuard.reject)
    event.listen(_append_only, "before_delete", AppendOnlyGuard.reject)
    event.listen(
        _append_only.__table__,
        "after_create",
        AppendOnlyGuard.sqlite_triggers(_append_only.__tablename__),
    )
