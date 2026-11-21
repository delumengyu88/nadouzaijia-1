from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from nadou.db import AwareDateTime, Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(AwareDateTime, nullable=False)
    entitlements: Mapped[list["Entitlement"]] = relationship(back_populates="user")


class Entitlement(Base):
    __tablename__ = "entitlements"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('regular', 'vip', 'program_90', 'program_30', 'screenshot')",
            name="ck_entitlements_kind",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    user: Mapped[User] = relationship(back_populates="entitlements")
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    program_name: Mapped[str | None] = mapped_column(String(200))
    record_id: Mapped[str | None] = mapped_column(String(36))
    expires_at: Mapped[datetime] = mapped_column(AwareDateTime, nullable=False)


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
