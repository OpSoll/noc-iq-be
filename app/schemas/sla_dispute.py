from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.sla_dispute import DisputeStatus


class DisputeFlagRequest(BaseModel):
    flagged_by: str = Field(..., description="Identifier of the operator flagging the dispute")
    dispute_reason: str = Field(..., min_length=10, description="Reason for disputing the SLA calculation")


class DisputeResolveRequest(BaseModel):
    resolved_by: str = Field(..., description="Identifier of the operator resolving the dispute")
    resolution_notes: str = Field(..., min_length=10, description="Notes explaining the resolution decision")
    status: DisputeStatus = Field(..., description="Resolution outcome: resolved or rejected")


class DisputeResponse(BaseModel):
    id: UUID
    sla_result_id: UUID
    flagged_by: str
    dispute_reason: str
    flagged_at: datetime
    status: DisputeStatus
    resolved_by: Optional[str] = None
    resolution_notes: Optional[str] = None
    resolved_at: Optional[datetime] = None

    class Config:
        from_attributes = True
