from pydantic import BaseModel
from typing import List, Optional
from app.models.outage_dto import OutageCreate

class BulkOutageCreate(BaseModel):
    outages: List[OutageCreate]

class BulkOutageResponse(BaseModel):
    successful: int
    failed: int
    message: str
