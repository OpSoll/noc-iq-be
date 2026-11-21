from datetime import datetime
from enum import Enum
from typing import Any, Dict, FrozenSet, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# Maximum time a transaction can remain unsubmitted (seconds)
TRANSACTION_TIMEOUT_MAX_SECONDS = 300  # 5 minutes
# Default expiry window for unconfirmed transactions
TRANSACTION_EXPIRY_WINDOW_SECONDS = TRANSACTION_TIMEOUT_MAX_SECONDS


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


class PaymentIdempotencyError(ValueError):
    """Raised when a duplicate payment attempt matches an active idempotency key.

    Issue #560: deterministic Stellar payment idempotency keys are used to
    reject duplicate payouts for the same (outage, amount, recipient) tuple
    before a second row can be inserted (the DB unique constraint is the
    final backstop).
    """

    def __init__(self, idempotency_key: str, message: str | None = None) -> None:
        self.idempotency_key = idempotency_key
        super().__init__(
            message
            or f"Duplicate payment attempt rejected for idempotency key "
            f"'{idempotency_key}'"
        )


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
            f"Allowed: {sorted(allowed)}"
        )


def validate_transition(current: str, next_status: str) -> None:
    """Validate that a status transition is allowed.

    Raises PaymentTransitionError if the transition is not in VALID_TRANSITIONS.
    """
    try:
        current_enum = PaymentStatus(current)
    except ValueError:
        raise PaymentTransitionError(
            current=current,
            next_status=next_status,
            allowed=set(),
        )
    try:
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


class TimeBounds(BaseModel):
    """Stellar transaction time bounds for timeout enforcement.

    min_time: Earliest valid submission time (0 = no lower bound).
    max_time: Latest valid submission time (transaction expires after this).

    Invalid configurations (negative values, or max_time < min_time when set)
    are rejected at construction time for security-relevant expiry rules.
    """
    min_time: int = 0
    max_time: int = 0

    @field_validator("min_time", "max_time")
    @classmethod
    def non_negative_bounds(cls, v: int) -> int:
        if v < 0:
            raise ValueError("time bound values must be non-negative")
        return v

    @model_validator(mode="after")
    def max_ge_min_when_set(self) -> "TimeBounds":
        # max_time == 0 means "no upper bound set" (unset); allow any min_time.
        if self.max_time > 0 and self.max_time < self.min_time:
            raise ValueError("max_time must be greater than or equal to min_time")
        return self

    @classmethod
    def default_for_transaction(cls, now_utc: Optional[datetime] = None) -> "TimeBounds":
        """Create default time bounds: min=0, max=now+300s.

        Transactions built with these bounds remain valid for at most 5 minutes
        and are automatically expired if not submitted within that window.
        """
        from datetime import timezone
        if now_utc is None:
            now_utc = datetime.now(timezone.utc)
        return cls(
            min_time=0,
            max_time=int(now_utc.timestamp()) + TRANSACTION_TIMEOUT_MAX_SECONDS,
        )

    def is_expired(self, now_utc: Optional[datetime] = None) -> bool:
        """Return True if current time exceeds max_time."""
        from datetime import timezone
        if now_utc is None:
            now_utc = datetime.now(timezone.utc)
        return int(now_utc.timestamp()) > self.max_time


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
                "time_bounds_min": 0,
                "time_bounds_max": 1735689600,
            }
        }
    )

    id: str
    transaction_hash: str
    type: str
    amount: float
    asset_code: str
    asset_issuer: Optional[str] = None
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
    idempotency_key: Optional[str] = None
    dead_letter_reason: Optional[str] = None
    dead_lettered_at: Optional[datetime] = None
    residual: float = 0.0
    # Transaction timeout bounds (Stellar time_bounds)
    time_bounds_min: int = 0
    time_bounds_max: int = 0
    # Whether the transaction is pending fee re-estimation after expiry
    fee_re_estimation_pending: bool = False
    expired_at: Optional[datetime] = None


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


class ReconciliationReport(BaseModel):
    """Summary of a payment reconciliation run."""
    matched: int = 0
    delayed: int = 0
    missing: int = 0
    divergent: int = 0
    items: List[Dict[str, Any]] = Field(default_factory=list)
