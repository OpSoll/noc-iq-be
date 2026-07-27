from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

class OutageItemCreate(BaseModel):
    service_name: str
    description: str
    severity: str

class BulkOutageCreate(BaseModel):
    outages: List[OutageItemCreate]

class BulkOutageResponse(BaseModel):
    successful: int
    failed: int
    message: str
