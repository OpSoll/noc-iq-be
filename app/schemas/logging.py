from pydantic import BaseModel
from typing import Dict, Any, Optional
from datetime import datetime

class RequestLogBase(BaseModel):
    method: str
    path: str
    status_code: int
    payload: Optional[Dict[str, Any]] = None

class RequestLogCreate(RequestLogBase):
    pass

class RequestLogResponse(RequestLogBase):
    id: int
    timestamp: datetime
    
    class Config:
        orm_mode = True
