from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

class SiteHierarchyBase(BaseModel):
    name: str
    region: str
    parent_id: Optional[int] = None

class SiteHierarchyCreate(SiteHierarchyBase):
    pass

class SiteHierarchyResponse(SiteHierarchyBase):
    id: int
    created_at: datetime
    
    class Config:
        orm_mode = True
