from typing import Literal, Optional

from pydantic import BaseModel, Field
from app.models.enums import Severity


class SLAPreviewRequest(BaseModel):
    severity: Severity
    mttr_minutes: int


class SLAResult(BaseModel):
    id: Optional[int] = None
    outage_id: str
    status: Literal["met", "violated"]
    mttr_minutes: int
    threshold_minutes: int
    amount: int
    payment_type: Literal["reward", "penalty"]
    rating: Literal["exceptional", "excellent", "good", "poor"]


class SLASeverityConfig(BaseModel):
    threshold_minutes: int = Field(..., ge=0)
    penalty_per_minute: int = Field(..., ge=0)
    reward_base: int = Field(..., ge=0)


class SLAConfigUpdateRequest(SLASeverityConfig):
    pass


class SLAPerformanceAggregation(BaseModel):
    total_outages: int = Field(ge=0)
    violation_rate: float = Field(ge=0.0, le=1.0)
    avg_mttr: float = Field(ge=0.0)
    payout_sum: float


class SLADashboardKPI(BaseModel):
    total_outages: int = Field(ge=0)
    total_violations: int = Field(ge=0)
    total_rewards: float = Field(ge=0.0)
    total_penalties: float = Field(ge=0.0)
    net_payout: float


class SLATrendPoint(BaseModel):
    date: str
    total_outages: int = Field(ge=0)
    violations: int = Field(ge=0)
    rewards: float = Field(ge=0.0)
    penalties: float = Field(ge=0.0)
