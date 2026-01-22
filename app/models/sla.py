from pydantic import BaseModel


class SLAResult(BaseModel):
    outage_id: str
    status: str  # "met" or "violated"
    mttr_minutes: int
    threshold_minutes: int
    amount: float  # negative = penalty, positive = reward
    payment_type: str  # "reward" or "penalty"
    rating: str  # "exceptional", "excellent", "good", "poor"