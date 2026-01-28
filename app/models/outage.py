from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field


class Location(BaseModel):
    latitude: float
    longitude: float


class SLAStatus(BaseModel):
    status: str  # "in_progress", "met", "violated"
    mttr_minutes: Optional[int] = None
    threshold_minutes: int
    time_remaining_minutes: Optional[int] = None


class Outage(BaseModel):
    id: str = Field(..., description="Unique outage ID")
    site_name: str
    site_id: Optional[str] = None
    severity: str  # critical, high, medium, low
    status: str  # active, resolved, investigating
    detected_at: datetime
    resolved_at: Optional[datetime] = None
    description: str
    affected_services: List[str]
    affected_subscribers: Optional[int] = None
    assigned_to: Optional[str] = None
    created_by: Optional[str] = None
    location: Optional[Location] = None
    sla_status: Optional[SLAStatus] = None

class ResolveOutageRequest(BaseModel):
    mttr_minutes: int