from datetime import datetime
from enum import Enum
from typing import Dict, FrozenSet, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class PaymentStatus(str, Enum):
    pending = "pending"
    confirmed = "confirmed"
    failed = "failed"


# Allowed transitions: from_status -> set of valid to_statuses
VALID_TRANSITIONS: Dict[PaymentStatus, FrozenSet[PaymentStatus]] = {
    PaymentStatus.pending: frozenset({PaymentStatus.confirmed, PaymentStatus.failed}),
    PaymentStatus.confirmed: frozenset(),
    PaymentStatus.failed: frozenset({PaymentStatus.pending}),
}


def validate_transition(current: str, next_status: str) -> None:
    """Raise ValueError if the transition is not allowed."""
    try:
        current_enum = PaymentStatus(current)
        next_enum = PaymentStatus(next_status)
    except ValueError:
        raise ValueError(f"Invalid payment status: '{next_status}'")
    if next_enum not in VALID_TRANSITIONS[current_enum]:
        allowed = {s.value for s in VALID_TRANSITIONS[current_enum]}
        raise ValueError(
            f"Transition from '{current}' to '{next_status}' is not allowed. "
            f"Allowed: {allowed or 'none'}"
        )


class PaymentTransaction(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "payment-001",
                "transaction_hash": "tx-123abc",
                "type": "reward",
                "amount": 150.0,
                "asset_code": "USDC",
                "from_address": "SYSTEM_POOL",
                "to_address": "OUTAGE_SETTLEMENT",
                "status": "confirmed",
                "outage_id": "outage-001",
                "sla_result_id": 1,
                "created_at": "2026-01-01T00:00:00Z",
                "confirmed_at": "2026-01-01T01:00:00Z",
                "retry_count": 0,
                "last_retried_at": None,
            }
        }
    )

    id: str
    transaction_hash: str
    type: str
    amount: float
    asset_code: str
    from_address: str
    to_address: str
    status: str
    outage_id: str
    sla_result_id: Optional[int] = None
    created_at: datetime
    confirmed_at: Optional[datetime] = None
    retry_count: int = 0
    last_retried_at: Optional[datetime] = None


class PaginatedPayments(BaseModel):
    items: List[PaymentTransaction]
    total: int
    page: int = Field(..., ge=1)
    page_size: int = Field(..., ge=1, le=100)
