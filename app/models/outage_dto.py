from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel

from .outage import Location


class OutageCreate(BaseModel):
    id: str
    site_name: str
    site_id: Optional[str] = None
    severity: str
    status: str
    detected_at: datetime
    description: str
    affected_services: List[str]
    affected_subscribers: Optional[int] = None
    assigned_to: Optional[str] = None
    created_by: Optional[str] = None
    location: Optional[Location] = None


class OutageUpdate(BaseModel):
    site_name: Optional[str] = None
    severity: Optional[str] = None
    status: Optional[str] = None
    resolved_at: Optional[datetime] = None
    description: Optional[str] = None
    affected_services: Optional[List[str]] = None
    affected_subscribers: Optional[int] = None
    assigned_to: Optional[str] = None