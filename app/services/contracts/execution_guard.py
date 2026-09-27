from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)


class ExecutionBlockedError(RuntimeError):
    def __init__(self, reason: str, check: str) -> None:
        self.reason = reason
        self.check = check
        super().__init__(f"Execution blocked [{check}]: {reason}")


@dataclass
class GuardCheckResult:
    passed: bool
    check: str
    detail: str = ""


class ExecutionGuard:
    def __init__(self) -> None:
        self._rate_tracker: Dict[str, List[float]] = {}

    def pre_check(self, params: Dict[str, Any]) -> GuardCheckResult:
        result = self._check_execution_mode(params)
        if not result.passed:
            return result

        result = self._check_contract_address(params)
        if not result.passed:
            return result

        result = self._check_network(params)
        if not result.passed:
            return result

        result = self._check_amount(params)
        if not result.passed:
            return result

        result = self._check_rate_limit(params)
        if not result.passed:
            return result

        return GuardCheckResult(passed=True, check="all")

    def post_check(self, result: Any) -> GuardCheckResult:
        if result is None:
            return GuardCheckResult(
                passed=False,
                check="post_execution",
                detail="Contract returned null result",
            )
        return GuardCheckResult(passed=True, check="post_execution")

    def _check_execution_mode(self, params: Dict[str, Any]) -> GuardCheckResult:
        intended_mode = params.get("execution_mode")
        configured = settings.CONTRACT_EXECUTION_MODE
        if intended_mode and intended_mode != configured:
            return GuardCheckResult(
                passed=False,
                check="execution_mode",
                detail=(
                    f"Intended mode '{intended_mode}' does not match "
                    f"configured mode '{configured}'"
                ),
            )
        return GuardCheckResult(passed=True, check="execution_mode")

    def _check_contract_address(self, params: Dict[str, Any]) -> GuardCheckResult:
        address = params.get("contract_address", "")
        allowed = settings.ALLOWED_CONTRACT_ADDRESSES
        if allowed and address and address not in allowed:
            return GuardCheckResult(
                passed=False,
                check="contract_address",
                detail=f"Contract address '{address}' is not in the allowed list",
            )
        return GuardCheckResult(passed=True, check="contract_address")

    def _check_network(self, params: Dict[str, Any]) -> GuardCheckResult:
        target_network = params.get("network")
        configured = settings.STELLAR_NETWORK
        if target_network and target_network != configured:
            return GuardCheckResult(
                passed=False,
                check="network",
                detail=(
                    f"Target network '{target_network}' does not match "
                    f"configured network '{configured}'"
                ),
            )
        return GuardCheckResult(passed=True, check="network")

    def _check_amount(self, params: Dict[str, Any]) -> GuardCheckResult:
        amount = params.get("amount")
        if amount is not None:
            max_amount = settings.MAX_CONTRACT_EXECUTION_AMOUNT
            if max_amount > 0 and float(amount) > max_amount:
                return GuardCheckResult(
                    passed=False,
                    check="amount",
                    detail=(
                        f"Amount {amount} exceeds maximum allowed "
                        f"contract execution amount {max_amount}"
                    ),
                )
        return GuardCheckResult(passed=True, check="amount")

    def _check_rate_limit(self, params: Dict[str, Any]) -> GuardCheckResult:
        user_id = params.get("user_id", "anonymous")
        now = time.monotonic()
        window = 60.0
        max_calls = settings.CONTRACT_CALL_RATE_LIMIT

        timestamps = self._rate_tracker.get(user_id, [])
        timestamps = [t for t in timestamps if now - t < window]
        self._rate_tracker[user_id] = timestamps

        if len(timestamps) >= max_calls:
            return GuardCheckResult(
                passed=False,
                check="rate_limit",
                detail=(
                    f"Rate limit exceeded: {len(timestamps)}/{max_calls} "
                    f"calls in the last {int(window)}s for user '{user_id}'"
                ),
            )

        timestamps.append(now)
        self._rate_tracker[user_id] = timestamps
        return GuardCheckResult(passed=True, check="rate_limit")
