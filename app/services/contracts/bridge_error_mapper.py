from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from fastapi import HTTPException, status

from app.core.config import settings

logger = logging.getLogger(__name__)


class BridgeErrorCode(str, Enum):
    TIMEOUT = "TIMEOUT"
    REJECTED = "REJECTED"
    INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"
    NETWORK_ERROR = "NETWORK_ERROR"
    CONTRACT_ERROR = "CONTRACT_ERROR"
    UNKNOWN = "UNKNOWN"


@dataclass
class BridgeError:
    code: BridgeErrorCode
    message: str
    retryable: bool
    upstream_error: Optional[Any] = None


@dataclass
class BridgeTimeoutConfig:
    sla_check_ms: int = 5000
    payment_ms: int = 30000
    balance_check_ms: int = 10000

    def for_operation(self, operation: str) -> int:
        mapping = {
            "sla_check": self.sla_check_ms,
            "payment": self.payment_ms,
            "balance_check": self.balance_check_ms,
        }
        return mapping.get(operation, self.payment_ms)


_TIMEOUT_CONFIG = BridgeTimeoutConfig(
    sla_check_ms=settings.BRIDGE_TIMEOUT_SLA_CHECK_MS,
    payment_ms=settings.BRIDGE_TIMEOUT_PAYMENT_MS,
    balance_check_ms=settings.BRIDGE_TIMEOUT_BALANCE_MS,
)


def get_timeout_config() -> BridgeTimeoutConfig:
    return _TIMEOUT_CONFIG


_TIMEOUT_KEYWORDS = ("timeout", "timed out", "deadline exceeded", "read timed out")
_NETWORK_KEYWORDS = ("connection", "dns", "reset", "refused", "unreachable", "network")
_REJECTED_KEYWORDS = ("rejected", "denied", "forbidden", "unauthorized", "not allowed")
_FUNDS_KEYWORDS = ("insufficient", "not enough", "balance", "insufficient funds")
_CONTRACT_KEYWORDS = ("contract", "soroban", "invoke", "wasm", "soroban_rpc")


def map_bridge_error(raw_error: Any) -> BridgeError:
    error_str = str(raw_error).lower()

    if any(kw in error_str for kw in _TIMEOUT_KEYWORDS):
        return BridgeError(
            code=BridgeErrorCode.TIMEOUT,
            message=str(raw_error),
            retryable=True,
            upstream_error=raw_error,
        )

    if any(kw in error_str for kw in _NETWORK_KEYWORDS):
        return BridgeError(
            code=BridgeErrorCode.NETWORK_ERROR,
            message=str(raw_error),
            retryable=True,
            upstream_error=raw_error,
        )

    if any(kw in error_str for kw in _REJECTED_KEYWORDS):
        return BridgeError(
            code=BridgeErrorCode.REJECTED,
            message=str(raw_error),
            retryable=False,
            upstream_error=raw_error,
        )

    if any(kw in error_str for kw in _FUNDS_KEYWORDS):
        return BridgeError(
            code=BridgeErrorCode.INSUFFICIENT_FUNDS,
            message=str(raw_error),
            retryable=False,
            upstream_error=raw_error,
        )

    if any(kw in error_str for kw in _CONTRACT_KEYWORDS):
        return BridgeError(
            code=BridgeErrorCode.CONTRACT_ERROR,
            message=str(raw_error),
            retryable=False,
            upstream_error=raw_error,
        )

    return BridgeError(
        code=BridgeErrorCode.UNKNOWN,
        message=str(raw_error),
        retryable=False,
        upstream_error=raw_error,
    )


# HTTP translation for bridge errors surfaced through the API. Retryable codes
# map to 503/504 and carry a Retry-After hint; terminal codes never do.
BRIDGE_ERROR_HTTP_STATUS: dict[BridgeErrorCode, int] = {
    BridgeErrorCode.TIMEOUT: status.HTTP_504_GATEWAY_TIMEOUT,
    BridgeErrorCode.NETWORK_ERROR: status.HTTP_503_SERVICE_UNAVAILABLE,
    BridgeErrorCode.REJECTED: status.HTTP_422_UNPROCESSABLE_ENTITY,
    BridgeErrorCode.INSUFFICIENT_FUNDS: status.HTTP_402_PAYMENT_REQUIRED,
    BridgeErrorCode.CONTRACT_ERROR: status.HTTP_502_BAD_GATEWAY,
    BridgeErrorCode.UNKNOWN: status.HTTP_500_INTERNAL_SERVER_ERROR,
}

BRIDGE_RETRY_AFTER_SECONDS = 5
UNKNOWN_BRIDGE_ERROR_MESSAGE = "Contract bridge returned an unrecognised error."


def http_status_for_bridge_code(code: Any) -> int:
    """Resolve the HTTP status for a bridge error code, falling back to 500."""
    try:
        return BRIDGE_ERROR_HTTP_STATUS[BridgeErrorCode(code)]
    except (ValueError, KeyError):
        return BRIDGE_ERROR_HTTP_STATUS[BridgeErrorCode.UNKNOWN]


def bridge_error_to_http(error: BridgeError) -> HTTPException:
    """Translate a BridgeError into an HTTPException without leaking upstream details."""
    try:
        code = BridgeErrorCode(error.code)
    except ValueError:
        code = BridgeErrorCode.UNKNOWN
    # Unclassified errors are never advertised as retryable and their raw text
    # is replaced, since it may carry upstream internals.
    is_unknown = code is BridgeErrorCode.UNKNOWN
    retryable = bool(error.retryable) and not is_unknown
    headers = {"Retry-After": str(BRIDGE_RETRY_AFTER_SECONDS)} if retryable else None
    return HTTPException(
        status_code=http_status_for_bridge_code(code),
        detail={
            "error_code": code.value,
            "message": UNKNOWN_BRIDGE_ERROR_MESSAGE if is_unknown else error.message,
            "retryable": retryable,
        },
        headers=headers,
    )
