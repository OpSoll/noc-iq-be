from __future__ import annotations

from typing import Any, Optional

from app.core.config import settings
from app.models.payment import RetryClass
from app.services.sla import SLACalculator


def check_blockchain_payment_status(transaction_id: str) -> Optional[dict[str, Any]]:
    return None


def classify_error(error: Exception) -> RetryClass:
    error_str = str(error).lower()
    if any(kw in error_str for kw in ("timeout", "connection", "dns", "reset")):
        return RetryClass.network
    if any(kw in error_str for kw in ("rate limit", "429", "too many")):
        return RetryClass.rate_limit
    if any(kw in error_str for kw in ("invalid", "bad request", "unauthorized", "forbidden", "not found")):
        return RetryClass.semantic
    return RetryClass.unknown


class SLAContractAdapter:
    """
    Backend-facing contract adapter.

    The current implementation uses the local calculator as a stand-in execution
    engine while exposing a contract-style response shape and centralized
    configuration for the eventual Soroban integration.
    """

    @staticmethod
    def get_runtime_metadata() -> dict[str, str]:
        return {
            "contract_address": settings.SLA_CONTRACT_ADDRESS,
            "network": settings.STELLAR_NETWORK,
            "execution_mode": settings.CONTRACT_EXECUTION_MODE,
        }

    @classmethod
    def calculate_sla(cls, outage_id: str, severity: str, mttr_minutes: int, policy_version: str = "1.0", threshold_source: str = "config") -> dict[str, Any]:
        local_result = SLACalculator.calculate(
            outage_id=outage_id,
            severity=severity,
            mttr_minutes=mttr_minutes,
            policy_version=policy_version,
            threshold_source=threshold_source,
        )

        rating_code_map = {
            "exceptional": "top",
            "excellent": "high",
            "good": "good",
            "poor": "poor",
        }

        return {
            "outage_id": local_result.outage_id,
            "status": "viol" if local_result.status == "violated" else "met",
            "mttr_minutes": local_result.mttr_minutes,
            "threshold_minutes": local_result.threshold_minutes,
            "amount": local_result.amount,
            "payment_type": "pen" if local_result.payment_type == "penalty" else "rew",
            "rating": rating_code_map[local_result.rating],
            "contract_metadata": cls.get_runtime_metadata(),
        }
