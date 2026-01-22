from datetime import datetime
from pydantic import BaseModel
from .outage import Outage, Location, SLAStatus
from .sla import SLAResult
from .payment import PaymentTransaction
from .wallet import Wallet


class PaymentTransaction(BaseModel):
    id: str
    transaction_hash: str
    type: str  # "reward", "penalty", "manual"
    amount: float
    asset_code: str  # USDC, XLM, etc
    from_address: str
    to_address: str
    status: str  # "pending", "confirmed", "failed"
    outage_id: str
    created_at: datetime
    confirmed_at: datetime | None = None

    