from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from enum import Enum

class DisputeState(str, Enum):
    OPEN = "OPEN"
    UNDER_REVIEW = "UNDER_REVIEW"
    RESOLVED = "RESOLVED"
    REJECTED = "REJECTED"

class DisputeBase(BaseModel):
    sla_id: int
    reason: str
    evidence_url: Optional[str] = None

class DisputeCreate(DisputeBase):
    pass

class DisputeUpdate(BaseModel):
    state: DisputeState
    resolution_notes: Optional[str] = None


class ReSimulateRequest(BaseModel):
    """Inputs for re-simulating an SLA dispute's contract calculation (Issue #510)."""

    mttr_minutes: int = Field(..., ge=0, description="Updated MTTR (minutes) to dry-run against the Soroban contract")
    severity: Optional[str] = Field(default=None, description="Optional severity override for the dry-run")

class DisputeResponse(DisputeBase):
    id: int
    state: DisputeState
    resolution_notes: Optional[str] = None
    created_at: datetime
    
    class Config:
        orm_mode = True
