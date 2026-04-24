from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

from app.models.enums import OutageStatus, Severity

from .outage import Location


class OutageSortField(str, Enum):
    detected_at = "detected_at"
    site_name = "site_name"
    severity = "severity"
    status = "status"
    id = "id"


class OutageSortDirection(str, Enum):
    asc = "asc"
    desc = "desc"


class OutageCreate(BaseModel):
    id: str = Field(..., min_length=1)
    site_name: str = Field(..., min_length=1)
    site_id: Optional[str] = None
    severity: Severity
    status: OutageStatus
    detected_at: datetime
    description: str = Field(..., min_length=1)
    affected_services: List[str] = Field(..., min_length=1)
    affected_subscribers: Optional[int] = Field(default=None, ge=0)
    assigned_to: Optional[str] = None
    created_by: Optional[str] = None
    location: Optional[Location] = None

    @field_validator("affected_services")
    @classmethod
    def services_not_empty(cls, v: List[str]) -> List[str]:
        if not v:
            raise ValueError("affected_services must contain at least one entry")
        return v


class OutageUpdate(BaseModel):
    site_name: Optional[str] = None
    severity: Optional[Severity] = None
    status: Optional[OutageStatus] = None
    resolved_at: Optional[datetime] = None
    description: Optional[str] = None
    affected_services: Optional[List[str]] = None
    affected_subscribers: Optional[int] = None
    assigned_to: Optional[str] = None
    created_by: Optional[str] = None
    location: Optional[Location] = None


class BulkOutageCreate(BaseModel):
    outages: List[OutageCreate]
