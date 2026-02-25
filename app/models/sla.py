from typing import Literal

from pydantic import BaseModel, Field
from app.models.enums import Severity


class SLAPreviewRequest(BaseModel):
    severity: Severity
    mttr_minutes: int

class SLAResult(BaseModel):
    outage_id: str
    status: Literal["met", "violated"]
    mttr_minutes: int
    threshold_minutes: int
    amount: int
    payment_type: Literal["reward", "penalty"]
    rating: Literal["exceptional", "excellent", "good", "poor"]


class SLAPerformanceAggregation(BaseModel):
    total_outages: int = Field(ge=0)
    violation_rate: float = Field(ge=0.0, le=1.0)
    avg_mttr: float = Field(ge=0.0)
    payout_sum: float
