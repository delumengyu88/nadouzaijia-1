from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer


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
    access: Literal["lowres", "full", "screenshot", "summary"]
    summary_only: bool | None = None
    checked_on: str | None = None
    can_download_report: bool
    can_download_screenshot: bool
    platform: str | None = None
    platform_province: str | None = None
    platform_operator: str | None = None
    platform_subtype: str | None = None
    network_type: str | None = None
    hit_label: str | None = None
    frame_count: int | None = None
    timestamps: list[str] | None = None
    checked_at: str | None = None
    thumbnail_url: str | None = None
    hd_url: str | None = None
    category: str | None = None
    # What GET /api/v1/records/{id}/frame returns for this caller: clear or blurred.
    frame_view: Literal["clear", "blur"] | None = None


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


class PlatformGroupOut(BaseModel):
    """One province+operator bucket inside a single program title."""

    model_config = ConfigDict(extra="forbid")

    platform: str
    platform_province: str | None = None
    platform_operator: str | None = None
    records: list[OpenRecordOut]


class BandUnlockOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product: Literal["unlock_band_90_180", "unlock_band_30_90"]
    amount_fen: int
    price_text: str
    purchasable: bool


class BandOut(BaseModel):
    """One age band on the result page. Counts cover normal (non-summary) records."""

    model_config = ConfigDict(extra="forbid")

    band: Literal["ge_180", "d90_179", "d30_89", "lt_30"]
    label: str
    count: int
    state: Literal["open", "locked", "evidence"]
    frame_view: Literal["clear", "blur"] | None = None
    unlocked_by: Literal["membership", "unlock", "free"] | None = None
    unlock: BandUnlockOut | None = None
    upgrade_hint: str | None = None


class SearchOut(BaseModel):
    program_name: str
    outcome: Literal["hit", "miss", "recent_only", "unverified"]
    platform_count: int
    unverified_source_count: int
    locked_summary: LockedSummaryOut
    summary_only_count: int | None = None
    summary_hidden_count: int | None = None
    summary_unlocks_by_product: dict[str, int] | None = None
    platform_groups: list[PlatformGroupOut]
    locked_records: list[LockedRecordOut]
    # Recognized platforms of visible rows that may prefill 委托取证.
    # Rows with a generated date are never included.
    evidence_platforms: list[str] = []
    evidence_program_name: str = ""
    # Result-page bands. Absent on older servers; clients fall back to the flat list.
    viewer_tier: Literal["free", "regular", "vip"] | None = None
    bands: list[BandOut] | None = None
    frames_ready: bool | None = None
    report_ready: bool | None = None


class BatchNames(BaseModel):
    names: list[str] = Field(min_length=1, max_length=200)


class BatchSearchIn(BatchNames):
    query_id: str | None = Field(default=None, min_length=1, max_length=64)
