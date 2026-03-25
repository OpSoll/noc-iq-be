from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class PaymentTransaction(BaseModel):
    id: str
    transaction_hash: str
    type: str
    amount: float
    asset_code: str
    from_address: str
    to_address: str
    status: str
    outage_id: str
    created_at: datetime
    confirmed_at: Optional[datetime] = None


class PaginatedPayments(BaseModel):
    items: List[PaymentTransaction]
    total: int
    page: int = Field(..., ge=1)
    page_size: int = Field(..., ge=1, le=100)
