from typing import Literal

from pydantic import BaseModel, ConfigDict, model_serializer


class LockedRecordOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_id: str
    age_bucket: Literal["lt_30", "d30_89", "d90_179", "ge_180", "unknown"]
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
    frame_count: int | None = None
    timestamps: list[str] | None = None
    checked_at: str | None = None
    thumbnail_url: str | None = None
    hd_url: str | None = None


class TierCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    member: int
    vip: int
    evidence_only: int


class LockedSummaryOut(BaseModel):
    """Server-side lock counts. `cheapest_full_unlock` stays null, not omitted."""

    model_config = ConfigDict(extra="forbid")

    locked_total: int
    by_required_tier: TierCounts
    unlockable_total: int
    cheapest_full_unlock: Literal["single_program_30d", "single_program_90d"] | None
    unlocks_by_product: dict[str, int]

    @model_serializer(mode="wrap")
    def _keep_null_product(self, handler):
        data = handler(self)
        data["cheapest_full_unlock"] = self.cheapest_full_unlock
        return data


class SearchOut(BaseModel):
    program_name: str
    outcome: Literal["hit", "miss", "recent_only"]
    platform_count: int
    unverified_source_count: int
    locked_summary: LockedSummaryOut
    records: list[LockedRecordOut | OpenRecordOut]
