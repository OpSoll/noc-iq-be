from pydantic import BaseModel
from typing import Dict, Any

class DependencyStatus(BaseModel):
    status: str
    latency_ms: int
    details: Dict[str, Any] = {}

class ReadinessResponse(BaseModel):
    status: str
    dependencies: Dict[str, DependencyStatus]
