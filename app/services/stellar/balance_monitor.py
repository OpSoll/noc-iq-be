"""Stellar wallet XLM/USDC balance threshold monitor with alert trigger.

If the settlement wallet runs dry, every SLA payout fails — and the first
sign of it today is a wave of failed transactions. This monitor reads the
operator wallet's balances from Horizon on a 15-minute beat, raises an alert
as soon as XLM drops below 50 or USDC below $500, and caches the result in
Redis so ``GET /api/v1/health/detailed`` can report wallet health without
making a chain call on the request path.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

import httpx

from app.core.config import Settings, settings as app_settings

UTC = timezone.utc

logger = logging.getLogger(__name__)

WALLET_BALANCE_REDIS_KEY = "stellar:wallet:balance:health"
# TTL is twice the beat interval so a stale entry is detectable rather than
# silently served as current.
WALLET_BALANCE_TTL_SECONDS = 1800

STATUS_OK = "ok"
STATUS_LOW = "low"
STATUS_UNKNOWN = "unknown"

NATIVE_ASSET_CODE = "XLM"


@dataclass
class BalanceThresholdBreach:
    """A single asset whose balance is below its operational threshold."""

    asset_code: str
    balance: Decimal
    threshold: Decimal

    @property
    def shortfall(self) -> Decimal:
        return self.threshold - self.balance

    def as_dict(self) -> dict[str, Any]:
        return {
            "asset_code": self.asset_code,
            "balance": str(self.balance),
            "threshold": str(self.threshold),
            "shortfall": str(self.shortfall),
        }


@dataclass
class WalletBalanceHealth:
    """Wallet balance snapshot plus threshold evaluation."""

    address: str
    status: str
    checked_at: datetime
    balances: dict[str, Decimal] = field(default_factory=dict)
    thresholds: dict[str, Decimal] = field(default_factory=dict)
    breaches: list[BalanceThresholdBreach] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def healthy(self) -> bool:
        return self.status == STATUS_OK

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe metrics payload for the health endpoint and Redis."""
        return {
            "address": self.address,
            "status": self.status,
            "healthy": self.healthy if self.status != STATUS_UNKNOWN else None,
            "checked_at": self.checked_at.isoformat(),
            "balances": {k: str(v) for k, v in self.balances.items()},
            "thresholds": {k: str(v) for k, v in self.thresholds.items()},
            "breaches": [b.as_dict() for b in self.breaches],
            "error": self.error,
        }


class WalletBalanceMonitor:
    """Reads operator wallet balances and evaluates them against thresholds."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or app_settings

    # ── Configuration ────────────────────────────────────────────────────

    @property
    def address(self) -> str:
        return self._settings.monitored_wallet_address

    def thresholds(self) -> dict[str, Decimal]:
        return {
            NATIVE_ASSET_CODE: Decimal(str(self._settings.WALLET_MIN_XLM_BALANCE)),
            self._settings.PAYMENT_ASSET_CODE: Decimal(
                str(self._settings.WALLET_MIN_USDC_BALANCE)
            ),
        }

    # ── Horizon read ─────────────────────────────────────────────────────

    async def fetch_balances(self, address: str) -> dict[str, Decimal]:
        """Return ``{asset_code: balance}`` for *address* from Horizon.

        A missing account (404) reads as zero balances rather than an error:
        an operator wallet that is not on the ledger cannot pay out, which is
        precisely the condition the alert exists for.
        """
        url = f"{self._settings.horizon_url}/accounts/{address}"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
            if resp.status_code == 404:
                return {code: Decimal("0") for code in self.thresholds()}
            resp.raise_for_status()
            data = resp.json()

        balances: dict[str, Decimal] = {}
        for entry in data.get("balances", []):
            code = (
                NATIVE_ASSET_CODE
                if entry.get("asset_type") == "native"
                else entry.get("asset_code")
            )
            if not code:
                continue
            try:
                balances[code] = Decimal(str(entry.get("balance", "0")))
            except (InvalidOperation, TypeError):
                balances[code] = Decimal("0")

        # An asset with no trustline holds nothing.
        for code in self.thresholds():
            balances.setdefault(code, Decimal("0"))
        return balances

    # ── Evaluation ───────────────────────────────────────────────────────

    def evaluate(self, balances: dict[str, Decimal]) -> list[BalanceThresholdBreach]:
        """Return every monitored asset sitting below its threshold."""
        breaches: list[BalanceThresholdBreach] = []
        for code, threshold in self.thresholds().items():
            balance = balances.get(code, Decimal("0"))
            if balance < threshold:
                breaches.append(
                    BalanceThresholdBreach(
                        asset_code=code, balance=balance, threshold=threshold
                    )
                )
        return breaches

    async def check(self, address: Optional[str] = None) -> WalletBalanceHealth:
        """Read balances for *address* and evaluate them against thresholds."""
        target = (address or self.address).strip()
        thresholds = self.thresholds()
        now = datetime.now(UTC)

        if not target:
            return WalletBalanceHealth(
                address="",
                status=STATUS_UNKNOWN,
                checked_at=now,
                thresholds=thresholds,
                error="No wallet address configured (WALLET_MONITOR_ADDRESS).",
            )

        try:
            balances = await self.fetch_balances(target)
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning(
                "Wallet balance check failed | address=%s error=%s", target, exc
            )
            return WalletBalanceHealth(
                address=target,
                status=STATUS_UNKNOWN,
                checked_at=now,
                thresholds=thresholds,
                error=f"Balance fetch failed: {exc}",
            )

        breaches = self.evaluate(balances)
        return WalletBalanceHealth(
            address=target,
            status=STATUS_LOW if breaches else STATUS_OK,
            checked_at=now,
            balances=balances,
            thresholds=thresholds,
            breaches=breaches,
        )

    # ── Alerting ─────────────────────────────────────────────────────────

    def trigger_alert(self, health: WalletBalanceHealth) -> bool:
        """Raise an alert for a threshold breach.

        Always logs at ERROR; additionally POSTs the metrics payload to
        ``WALLET_ALERT_WEBHOOK_URL`` when one is configured. Returns True if
        an alert was raised.
        """
        if not health.breaches:
            return False

        summary = ", ".join(
            f"{b.asset_code} {b.balance} < {b.threshold}" for b in health.breaches
        )
        logger.error(
            "Wallet balance below operational threshold | address=%s breaches=%s",
            health.address,
            summary,
            extra={"audit": True},
        )

        webhook_url = (self._settings.WALLET_ALERT_WEBHOOK_URL or "").strip()
        if webhook_url:
            payload = {
                "alert": "stellar_wallet_balance_low",
                "network": self._settings.STELLAR_NETWORK,
                **health.as_dict(),
            }
            try:
                httpx.post(webhook_url, json=payload, timeout=10.0)
            except httpx.HTTPError as exc:
                logger.warning("Failed to POST wallet balance alert: %s", exc)
        return True

    # ── Cached health for the API ────────────────────────────────────────

    def _get_redis(self):
        import redis

        return redis.Redis.from_url(self._settings.REDIS_URL, decode_responses=True)

    def store_health(self, health: WalletBalanceHealth) -> bool:
        """Persist the latest wallet health snapshot in Redis with a TTL."""
        try:
            self._get_redis().set(
                WALLET_BALANCE_REDIS_KEY,
                json.dumps(health.as_dict()),
                ex=WALLET_BALANCE_TTL_SECONDS,
            )
            return True
        except Exception:
            logger.warning("Failed to persist wallet balance health to Redis")
            return False

    def read_health(self) -> dict[str, Any]:
        """Return the most recent snapshot recorded by the beat task.

        Never raises and never touches Horizon — the health endpoint must
        stay fast and must not fail because a wallet check is unavailable.
        """
        try:
            raw = self._get_redis().get(WALLET_BALANCE_REDIS_KEY)
            if raw:
                return json.loads(raw)
        except Exception:
            logger.warning("Failed to read wallet balance health from Redis")

        thresholds = self.thresholds()
        return {
            "address": self.address,
            "status": STATUS_UNKNOWN,
            "healthy": None,
            "checked_at": None,
            "balances": {},
            "thresholds": {k: str(v) for k, v in thresholds.items()},
            "breaches": [],
            "error": "No wallet balance check has been recorded yet.",
        }

    # ── Beat entry point ─────────────────────────────────────────────────

    async def run_check(self, address: Optional[str] = None) -> WalletBalanceHealth:
        """Check balances, alert on breach, and cache the result."""
        health = await self.check(address)
        self.trigger_alert(health)
        self.store_health(health)
        return health


# Module-level singleton — stateless, safe to share across requests.
wallet_balance_monitor = WalletBalanceMonitor()


def read_wallet_health() -> dict[str, Any]:
    """Cached wallet health metrics for ``GET /api/v1/health/detailed``."""
    return wallet_balance_monitor.read_health()
