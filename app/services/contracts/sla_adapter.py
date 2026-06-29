# app/services/contracts/sla_adapter.py
# Stellar SLA adapter.
# - Validates network identity before any chain operation (#286).
# - Checks trustline readiness before payout submission (#285).

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, UTC
from typing import Any, Callable, Literal, Optional

from app.core.config import settings
from app.services.sla import SLACalculator
from app.utils.cache import CacheResult, TTLCache

from app.core.config import Settings, StellarNetwork, get_settings

logger = logging.getLogger(__name__)


# ── Result types ──────────────────────────────────────────────────────────────

class TrustlineStatus(str, Enum):
    READY = "ready"
    MISSING = "missing"           # trustline not established
    LIMIT_ZERO = "limit_zero"     # trustline exists but limit is 0
    UNKNOWN = "unknown"           # Horizon unreachable


class NetworkMismatchError(RuntimeError):
    """Raised when a wallet or operation targets the wrong Stellar network (#286)."""

    def __init__(self, expected: StellarNetwork, actual: str) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"Network mismatch: instance is configured for '{expected.value}' "
            f"but operation targets '{actual}'. Cross-network operations are forbidden."
        )


class TrustlineError(RuntimeError):
    """Raised when trustline prerequisites are unmet (#285)."""

    # Non-retryable reason codes surfaced to callers
    REASON_MISSING = "TRUSTLINE_MISSING"
    REASON_LIMIT_ZERO = "TRUSTLINE_LIMIT_ZERO"
    REASON_UNKNOWN = "TRUSTLINE_CHECK_FAILED"

    def __init__(self, reason: str, address: str, asset_code: str) -> None:
        self.reason = reason
        self.address = address
        self.asset_code = asset_code
        super().__init__(
            f"Trustline check failed for {address}/{asset_code}: {reason}"
        )


@dataclass
class TrustlineResult:
    status: TrustlineStatus
    asset_code: str
    asset_issuer: str
    balance: str | None = None
    limit: str | None = None


# ── Adapter ───────────────────────────────────────────────────────────────────

class SLAAdapter:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._horizon = self._settings.horizon_url

    # ── Network identity guard (#286) ─────────────────────────────────────────

    def assert_network(self, wallet_network: str) -> None:
        """Reject any operation where wallet_network differs from the configured network.

        Audit-logs every rejection attempt.
        """
        expected = self._settings.STELLAR_NETWORK.value
        if wallet_network.lower() != expected:
            logger.warning(
                "Cross-network operation rejected | expected=%s actual=%s",
                expected,
                wallet_network,
                extra={"audit": True},
            )
            raise NetworkMismatchError(self._settings.STELLAR_NETWORK, wallet_network)

    # ── Trustline verification (#285) ─────────────────────────────────────────

    async def check_trustline(
        self,
        address: str,
        asset_code: str,
        asset_issuer: str,
    ) -> TrustlineResult:
        """Return trustline readiness for *address*/*asset_code*.

        Non-destructive — only reads from Horizon.
        """
        url = f"{self._horizon}/accounts/{address}"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url)
                resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return TrustlineResult(
                    status=TrustlineStatus.MISSING,
                    asset_code=asset_code,
                    asset_issuer=asset_issuer,
                )
            logger.error("Horizon error checking trustline: %s", exc)
            return TrustlineResult(
                status=TrustlineStatus.UNKNOWN,
                asset_code=asset_code,
                asset_issuer=asset_issuer,
            )
        except httpx.RequestError as exc:
            logger.error("Horizon request error: %s", exc)
            return TrustlineResult(
                status=TrustlineStatus.UNKNOWN,
                asset_code=asset_code,
                asset_issuer=asset_issuer,
            )

        data: dict[str, Any] = resp.json()
        balances: list[dict[str, Any]] = data.get("balances", [])

        for balance in balances:
            if (
                balance.get("asset_code") == asset_code
                and balance.get("asset_issuer") == asset_issuer
            ):
                limit = balance.get("limit", "0")
                status = (
                    TrustlineStatus.LIMIT_ZERO
                    if float(limit) == 0
                    else TrustlineStatus.READY
                )
                return TrustlineResult(
                    status=status,
                    asset_code=asset_code,
                    asset_issuer=asset_issuer,
                    balance=balance.get("balance"),
                    limit=limit,
                )

        return TrustlineResult(
            status=TrustlineStatus.MISSING,
            asset_code=asset_code,
            asset_issuer=asset_issuer,
        )

    async def assert_trustline_ready(
        self,
        address: str,
        asset_code: str,
        asset_issuer: str,
    ) -> TrustlineResult:
        """Check trustline and raise TrustlineError if not READY (#285)."""
        result = await self.check_trustline(address, asset_code, asset_issuer)

        if result.status == TrustlineStatus.READY:
            return result

        reason_map = {
            TrustlineStatus.MISSING: TrustlineError.REASON_MISSING,
            TrustlineStatus.LIMIT_ZERO: TrustlineError.REASON_LIMIT_ZERO,
            TrustlineStatus.UNKNOWN: TrustlineError.REASON_UNKNOWN,
        }
        raise TrustlineError(
            reason=reason_map[result.status],
            address=address,
            asset_code=asset_code,
        )


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
