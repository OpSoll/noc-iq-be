from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, UTC
from typing import Any, Callable, Literal, Optional

from app.core.config import settings
from app.services.sla import SLACalculator
from app.utils.cache import CacheResult, TTLCache


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


# ---------------------------------------------------------------------------
# Balance fetch adapter (#283)
# ---------------------------------------------------------------------------


@dataclass
class BalanceFetchError:
    """Typed error returned when a balance read degrades."""

    code: Literal["STALE_FALLBACK", "FETCH_FAILED"]
    detail: str


@dataclass
class BalanceFetchResult:
    """Result of a balance fetch including freshness metadata and any error.

    source values:
        "live"           -- freshly fetched from chain (or simulator)
        "cache"          -- valid cache hit within TTL
        "stale_fallback" -- TTL expired but live fetch failed; stale data served
    """

    address: str
    balances: dict
    source: Literal["live", "cache", "stale_fallback"]
    cached_at: Optional[datetime]
    ttl_remaining: Optional[int]   # seconds; None when freshly fetched with no prior TTL
    is_degraded: bool = False
    error: Optional[BalanceFetchError] = None


class BalanceFetchAdapter:
    """Wallet balance cache with freshness guarantees and fallback (#283).

    Fetch order:
        1. Return a valid cache entry if within TTL (source="cache").
        2. Call live_fetcher when the cache is missing, stale, or
           force_refresh=True (source="live").
        3. On live_fetcher failure, serve the expired cache entry as a
           stale fallback (source="stale_fallback", is_degraded=True).
        4. When no cached entry exists and live_fetcher fails, return a
           typed error result with empty balances (is_degraded=True).

    Cache invalidation:
        Explicit  -- call invalidate(address) after mutations that change
                     balance state (e.g. funding a wallet).
        TTL-based -- controlled by the ttl_seconds passed at construction
                     (default: WALLET_CACHE_TTL_SECONDS from settings).
    """

    def __init__(self, cache: Optional[TTLCache] = None) -> None:
        self._cache: TTLCache = (
            cache if cache is not None
            else TTLCache(ttl_seconds=settings.WALLET_CACHE_TTL_SECONDS)
        )

    def fetch(
        self,
        address: str,
        live_fetcher: Callable[[str], dict],
        force_refresh: bool = False,
    ) -> BalanceFetchResult:
        meta: Optional[CacheResult] = self._cache.get_with_meta(address)

        if not force_refresh and meta is not None and not meta.is_expired:
            return BalanceFetchResult(
                address=address,
                balances=meta.value["balances"],
                source="cache",
                cached_at=meta.value["cached_at"],
                ttl_remaining=int(meta.ttl_remaining),
            )

        now = datetime.now(UTC)
        try:
            balances = live_fetcher(address)
            self._cache.set(address, {"balances": balances, "cached_at": now})
            return BalanceFetchResult(
                address=address,
                balances=balances,
                source="live",
                cached_at=now,
                ttl_remaining=self._cache._ttl,
            )
        except Exception as exc:
            if meta is not None:
                return BalanceFetchResult(
                    address=address,
                    balances=meta.value["balances"],
                    source="stale_fallback",
                    cached_at=meta.value["cached_at"],
                    ttl_remaining=0,
                    is_degraded=True,
                    error=BalanceFetchError(
                        code="STALE_FALLBACK",
                        detail=f"Live fetch failed; serving stale data. Reason: {exc}",
                    ),
                )
            return BalanceFetchResult(
                address=address,
                balances={},
                source="live",
                cached_at=None,
                ttl_remaining=None,
                is_degraded=True,
                error=BalanceFetchError(
                    code="FETCH_FAILED",
                    detail=f"Balance fetch failed and no cached data is available. Reason: {exc}",
                ),
            )

    def invalidate(self, address: str) -> None:
        """Explicitly evict the cached entry for address after a balance-changing write."""
        self._cache.invalidate(address)


# Module-level singleton -- shared across all requests in this process.
balance_fetch_adapter = BalanceFetchAdapter()
