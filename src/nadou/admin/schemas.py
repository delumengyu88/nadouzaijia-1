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
