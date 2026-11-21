from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class LoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str
    password: str


class StaffCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str
    password: str
    role: Literal["support", "admin"]


class EvidenceCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str
    request_type: str
    application_number: str | None = None


class AmountIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: int = Field(gt=0)


class DeliveryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: str
    note: str | None = None


class FailIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str
    refund_method: str | None = None
    refund_amount: int | None = None


class NoteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str


class BanIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["temporary", "permanent"]
    reason: str
    until: datetime | None = None


class MembershipIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tier: Literal["regular", "vip"]
    days: int = Field(ge=1, le=3660)
    reason: str


class ReissueIn(BaseModel):
    """Empty on purpose. Tier and duration cannot be changed on reissue."""

    model_config = ConfigDict(extra="forbid")


class RefundIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str


class InviteRedeemIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str


class InviteBatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int = Field(ge=1, le=200)
    channel: str
    expires_at: datetime


class SplashIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    image_name: str = ""
    jump_label: str | None = None
    slogan: str = ""


class AnnouncementIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    body: str
    button_text: str
    jump_label: str | None = None
    starts_at: datetime
    ends_at: datetime
    frequency: Literal["once", "every"]
    online: bool = True
    id: str | None = None


class ExampleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    program_name: str
    record_id: str | None = None


class ExampleMoveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    direction: Literal["up", "down"]


class OnlineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    online: bool


class ImportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    csv: str
