from datetime import datetime
from pydantic import BaseModel


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
    confirmed_at: datetime | None = None