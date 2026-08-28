from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

class ChangelogEntryBase(BaseModel):
    version: str
    description: str
    breaking_changes: bool = False

class ChangelogEntryCreate(ChangelogEntryBase):
    pass

class ChangelogEntryResponse(ChangelogEntryBase):
    id: int
    release_date: datetime
    
    class Config:
        orm_mode = True
