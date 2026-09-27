from pydantic import BaseModel
from datetime import datetime

class IdempotencyKeyCreate(BaseModel):
    key: str
    endpoint: str

class IdempotencyKeyResponse(BaseModel):
    id: int
    key: str
    endpoint: str
    processed_at: datetime
    
    class Config:
        orm_mode = True
