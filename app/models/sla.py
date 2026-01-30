from pydantic import BaseModel
from typing import Literal

from pydantic import BaseModel
from app.models.enums import Severity


class SLAPreviewRequest(BaseModel):
    severity: Severity
    mttr_minutes: int

class SLAResult(BaseModel):
    outage_id: str
    status: Literal["met", "violated"]
    mttr_minutes: int
    threshold_minutes: int
    amount: int
    payment_type: Literal["reward", "penalty"]
    rating: Literal["exceptional", "excellent", "good", "poor"]
