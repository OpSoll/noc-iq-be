from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator



class Location(BaseModel):
    latitude: float = Field(..., description="Latitude coordinate of the affected site")
    longitude: float = Field(..., description="Longitude coordinate of the affected site")


class SLAStatus(BaseModel):
    status: str = Field(..., description="SLA outcome: in_progress, met, or violated")
    mttr_minutes: Optional[int] = Field(None, description="Mean time to resolve in minutes")
    threshold_minutes: int = Field(..., description="SLA threshold in minutes")
    time_remaining_minutes: Optional[int] = Field(None, description="Minutes remaining before SLA breach (met only)")


class Outage(BaseModel):
    id: str = Field(..., description="Unique outage ID")
    site_name: str = Field(..., description="Name of the affected site")
    site_id: Optional[str] = Field(None, description="Identifier of the affected site")
    severity: str = Field(..., description="Outage severity: critical, high, medium, low")
    status: str = Field(..., description="Outage status: open, active, investigating, resolved")
    detected_at: datetime = Field(..., description="Timestamp the outage was detected")
    resolved_at: Optional[datetime] = Field(None, description="Timestamp the outage was resolved")
    description: str = Field(..., description="Human-readable description of the outage")
    affected_services: List[str] = Field(..., description="List of affected service names")
    affected_subscribers: Optional[int] = Field(None, description="Number of affected subscribers")
    assigned_to: Optional[str] = Field(None, description="Owner/assignee of the outage")
    created_by: Optional[str] = Field(None, description="User or system that created the outage")
    location: Optional[Location] = Field(None, description="Geographic coordinates of the outage")
    sla_status: Optional[SLAStatus] = Field(None, description="Computed SLA status for the outage")
    deleted_at: Optional[datetime] = Field(None, description="Timestamp of soft deletion, if any")

    @field_validator("detected_at")
    @classmethod
    def validate_detected_at_timezone(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        elif v.tzinfo != timezone.utc:
            v = v.astimezone(timezone.utc)
        return v

    @field_validator("resolved_at")
    @classmethod
    def validate_resolved_at_timezone(cls, v: datetime | None) -> datetime | None:
        if v is None:
            return None
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        elif v.tzinfo != timezone.utc:
            v = v.astimezone(timezone.utc)
        return v


class PaginatedOutages(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "items": [
                    {
                        "id": "outage-001",
                        "site_name": "Site A",
                        "site_id": "site_1",
                        "severity": "high",
                        "status": "resolved",
                        "detected_at": "2023-10-01T12:00:00Z",
                        "resolved_at": "2023-10-01T12:45:00Z",
                        "description": "Fiber cut",
                        "affected_services": ["4G"],
                        "mttr_minutes": 45,
                        "assigned_to": None,
                        "created_by": "user1",
                        "location": {"latitude": 40.7128, "longitude": -74.0060},
                        "sla_status": "met"
                    }
                ],
                "total": 1,
                "limit": 50,
                "offset": 0
            }
        }
    )

    items: List[Outage] = Field(..., description="Outage records for the current page")
    total: int = Field(..., description="Total number of matching outage records")
    limit: int = Field(50, description="Maximum number of records returned (1-100)")
    offset: int = Field(0, ge=0, description="Number of records skipped before this page")
    sort_by: Optional[str] = Field("detected_at", description="Sort field used for the result set")
    sort_direction: Optional[str] = Field("desc", description="Sort direction used for the result set")


class ResolveOutageRequest(BaseModel):
    mttr_minutes: int = Field(..., ge=0, description="Mean time to resolve in minutes used for SLA computation")


class BulkResolveOutageRequest(BaseModel):
    outage_ids: List[str] = Field(
        ..., min_length=1, description="List of outage IDs to resolve in a single transaction"
    )
    resolution_notes: str = Field("", description="Optional notes recorded against each resolved outage")


class BulkResolveFailure(BaseModel):
    id: str = Field(..., description="Outage ID that could not be resolved")
    reason: str = Field(..., description="Machine-readable reason for the failure")


class BulkResolveOutageResponse(BaseModel):
    succeeded: List[str] = Field(..., description="Outage IDs successfully resolved")
    failed: List[BulkResolveFailure] = Field(
        default_factory=list, description="Outage IDs that could not be resolved with reasons"
    )
    total: int = Field(..., description="Total number of outage IDs requested")
    success_count: int = Field(..., description="Number of outages successfully resolved")
    failure_count: int = Field(..., description="Number of outages that failed to resolve")
    resolution_notes: str = Field("", description="Resolution notes applied to the batch")

