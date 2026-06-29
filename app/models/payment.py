from datetime import datetime
from enum import Enum
from typing import Any, Dict, FrozenSet, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class RetryClass(str, Enum):
    network = "network"
    rate_limit = "rate_limit"
    semantic = "semantic"
    unknown = "unknown"


class ReconciliationCategory(str, Enum):
    matched = "matched"
    delayed = "delayed"
    missing = "missing"
    divergent = "divergent"


class PaymentStatus(str, Enum):
    pending = "pending"
    confirmed = "confirmed"
    failed = "failed"
    dead_letter = "dead_letter"


# Allowed transitions: from_status -> set of valid to_statuses
VALID_TRANSITIONS: Dict[PaymentStatus, FrozenSet[PaymentStatus]] = {
    PaymentStatus.pending: frozenset({PaymentStatus.confirmed, PaymentStatus.failed}),
    PaymentStatus.confirmed: frozenset(),
    PaymentStatus.failed: frozenset({PaymentStatus.pending}),
    PaymentStatus.dead_letter: frozenset({PaymentStatus.pending}),
}


class PaymentTransitionError(ValueError):
    """Raised when a payment status transition is not allowed.

    Carries structured data so the API layer can produce a consistent,
    typed 422 response without re-parsing the error message.
    """

    def __init__(self, current: str, next_status: str, allowed: set[str]) -> None:
        self.current = current
        self.next_status = next_status
        self.allowed = allowed
        super().__init__(
            f"Transition from '{current}' to '{next_status}' is not allowed. "
            f"Allowed: {allowed or 'none'}"
        )


def validate_transition(current: str, next_status: str) -> None:
    """Raise :class:`PaymentTransitionError` if the transition is not allowed.

    This is the single authoritative policy for payment status transitions.
    All code paths – retry, reconcile, callback – MUST call this function
    rather than implementing their own transition logic.
    """
    try:
        current_enum = PaymentStatus(current)
        next_enum = PaymentStatus(next_status)
    except ValueError:
        raise PaymentTransitionError(
            current=current,
            next_status=next_status,
            allowed={s.value for s in VALID_TRANSITIONS.get(PaymentStatus(current), frozenset())}
            if current in PaymentStatus._value2member_map_ else set(),
        )
    if next_enum not in VALID_TRANSITIONS[current_enum]:
        allowed = {s.value for s in VALID_TRANSITIONS[current_enum]}
        raise PaymentTransitionError(current=current, next_status=next_status, allowed=allowed)


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
                "dead_letter_reason": None,
                "dead_lettered_at": None,
                "residual": 0.0,
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
    failure_taxonomy: Optional[str] = None
    dead_letter_reason: Optional[str] = None
    dead_lettered_at: Optional[datetime] = None
    residual: float = 0.0


class PaginatedPayments(BaseModel):
    items: List[PaymentTransaction]
    total: int
    page: int = Field(..., ge=1)
    page_size: int = Field(..., ge=1, le=100)


class PaymentResponse(BaseModel):
    data: Optional[Any] = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=lambda: {"correlation_id": None})


class PaginatedPaymentResponse(BaseModel):
    data: Optional[PaginatedPayments] = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=lambda: {"correlation_id": None})


class CursorPage(BaseModel):
    items: List[PaymentTransaction]
    next_cursor: Optional[str] = None
    has_more: bool = False
