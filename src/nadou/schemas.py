from typing import Literal

from pydantic import BaseModel, ConfigDict


class LockedRecordOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_id: str
    age_bucket: Literal["lt_30", "d30_89", "d90_179", "ge_180"]
    locked: Literal[True]
    lock_reason: str


class OpenRecordOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_id: str
    age_bucket: Literal["lt_30", "d30_89", "d90_179", "ge_180"]
    locked: Literal[False]
    access: Literal["lowres", "full", "screenshot"]
    can_download_report: bool
    can_download_screenshot: bool
    platform: str | None = None
    network_type: str | None = None
    hit_type: str | None = None
    frame_count: int | None = None
    timestamps: list[str] | None = None
    checked_at: str | None = None
    thumbnail_url: str | None = None
    hd_url: str | None = None


class SearchOut(BaseModel):
    program_name: str
    outcome: Literal["hit", "miss", "recent_only"]
    platform_count: int
    unverified_source_count: int
    records: list[LockedRecordOut | OpenRecordOut]
