from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field
from app.models.enums import Severity


class SLAState(str, Enum):
    in_progress = "in_progress"
    met = "met"
    violated = "violated"


class SLAStatusResponse(BaseModel):
    outage_id: str = Field(..., description="Unique outage ID the SLA status belongs to")
    state: SLAState = Field(..., description="SLA state: in_progress, met, or violated")
    mttr_minutes: Optional[int] = Field(None, description="Mean time to resolve in minutes")
    threshold_minutes: int = Field(..., description="SLA threshold in minutes")
    time_remaining_minutes: Optional[int] = Field(None, description="Minutes remaining before SLA breach (met only)")
    period_start: Optional[str] = Field(None, description="Start of the SLA evaluation window")
    period_end: Optional[str] = Field(None, description="End of the SLA evaluation window")


class SLAPreviewRequest(BaseModel):
    severity: Severity = Field(..., description="Outage severity used for the preview calculation")
    mttr_minutes: int = Field(..., ge=0, description="Mean time to resolve in minutes to preview")


class SLAResult(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": 1,
                "outage_id": "outage-001",
                "status": "met",
                "mttr_minutes": 30,
                "threshold_minutes": 60,
                "amount": 100,
                "payment_type": "reward",
                "rating": "excellent",
                "reason_code": "met_excellent",
                "decision_trace": "MTTR 30 < 60 threshold, performance ratio 50%"
            }
        }
    )

    id: Optional[int] = None
    outage_id: str
    status: Literal["met", "violated"]
    mttr_minutes: int
    threshold_minutes: int
    amount: int
    payment_type: Literal["reward", "penalty"]
    rating: Literal["exceptional", "excellent", "good", "poor"]
    policy_version: Optional[str] = Field("v1.0", description="Version of SLA policy used for this calculation")
    threshold_source: Optional[str] = Field("config", description="Source of threshold values (e.g., 'config', 'contract')")
    reason_code: Optional[str] = Field(None, description="Machine-readable reason code for the decision")
    decision_trace: Optional[str] = Field(None, description="Machine-readable decision trace for audit")
    asset_code: Optional[str] = Field(None, description="Asset code for payment (e.g., XLM, USDC)")
    asset_issuer: Optional[str] = Field(None, description="Asset issuer for non-native assets")


class SLASeverityConfig(BaseModel):
    threshold_minutes: int = Field(..., ge=0, description="SLA threshold in minutes for this severity")
    penalty_per_minute: int = Field(..., ge=0, description="Penalty amount charged per minute over the threshold")
    reward_base: int = Field(..., ge=0, description="Base reward amount for meeting the SLA")
    asset_code: str = Field("XLM", description="Asset code for payment (e.g., XLM, USDC)")
    asset_issuer: Optional[str] = Field(None, description="Asset issuer for non-native assets")


class SLAConfigUpdateRequest(SLASeverityConfig):
    """Update payload for a severity's SLA configuration."""


class SLAPerformanceAggregation(BaseModel):
    total_outages: int = Field(ge=0, description="Total outages in the aggregation window")
    violation_rate: float = Field(ge=0.0, le=1.0, description="Fraction of outages that violated SLA")
    avg_mttr: float = Field(ge=0.0, description="Average mean-time-to-resolve in minutes")
    payout_sum: float = Field(..., description="Net payout across the window")


class SLADashboardKPI(BaseModel):
    total_outages: int = Field(ge=0, description="Total outages in the KPI window")
    total_violations: int = Field(ge=0, description="Total SLA violations in the window")
    total_rewards: float = Field(ge=0.0, description="Total reward payouts in the window")
    total_penalties: float = Field(ge=0.0, description="Total penalty payouts in the window")
    net_payout: float = Field(..., description="Net payout (rewards minus penalties)")


class SLATrendPoint(BaseModel):
    date: str = Field(..., description="Bucket date (ISO format)")
    total_outages: int = Field(ge=0, description="Outages in the bucket")
    violations: int = Field(ge=0, description="SLA violations in the bucket")
    rewards: float = Field(ge=0.0, description="Reward payouts in the bucket")
    penalties: float = Field(ge=0.0, description="Penalty payouts in the bucket")


class SLAAnalyticsSnapshot(BaseModel):
    id: Optional[int] = Field(None, description="Snapshot row ID")
    snapshot_key: str = Field(..., description="Key the snapshot was materialized under")
    total_outages: int = Field(ge=0, description="Total outages captured in the snapshot")
    total_violations: int = Field(ge=0, description="Total SLA violations captured in the snapshot")
    total_rewards: float = Field(ge=0.0, description="Total reward payouts captured in the snapshot")
    total_penalties: float = Field(ge=0.0, description="Total penalty payouts captured in the snapshot")
    net_payout: float = Field(..., description="Net payout captured in the snapshot")
    avg_mttr: float = Field(ge=0.0, description="Average mean-time-to-resolve captured in the snapshot")
    checksum: str = Field(..., description="Integrity checksum over the snapshot payload")
    created_at: Optional[str] = Field(None, description="Timestamp the snapshot was created")