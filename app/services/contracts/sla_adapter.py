# app/services/contracts/sla_adapter.py
# Stellar SLA adapter.
# - Validates network identity before any chain operation (#286).
# - Checks trustline readiness before payout submission (#285).

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

import httpx

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