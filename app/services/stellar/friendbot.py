"""Stellar testnet Friendbot auto-faucet funding service.

Testnet resets wipe every account balance, which previously meant an
operator had to hit Friendbot by hand before payouts could resume. This
service requests Friendbot funding for an address and, via
:meth:`FriendbotService.fund_if_unfunded`, triggers that request
automatically whenever the account's native (XLM) balance is 0 — including
the case where the reset removed the account from the ledger entirely.

Friendbot only exists on the test networks; requests are refused on
mainnet so a misconfigured instance can never call a faucet that is not
there.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

import httpx

from app.core.config import Settings, settings as app_settings
from app.utils.wallet_address import WalletAddressError, normalize

UTC = timezone.utc

logger = logging.getLogger(__name__)

# Horizon reports a missing account with 404 — after a testnet reset an
# address that used to be funded looks exactly like one that never existed.
ACCOUNT_NOT_FOUND_BALANCE = Decimal("0")


class FriendbotError(RuntimeError):
    """Raised when a Friendbot funding request cannot be completed.

    ``reason`` carries a stable machine-readable code so callers can decide
    whether to retry without parsing the message.
    """

    REASON_DISABLED = "FRIENDBOT_DISABLED"
    REASON_UNSUPPORTED_NETWORK = "FRIENDBOT_UNSUPPORTED_NETWORK"
    REASON_INVALID_ADDRESS = "FRIENDBOT_INVALID_ADDRESS"
    REASON_REQUEST_FAILED = "FRIENDBOT_REQUEST_FAILED"

    # Network/transport failures are worth retrying; the rest are not.
    RETRYABLE_REASONS = frozenset({REASON_REQUEST_FAILED})

    def __init__(self, reason: str, detail: str) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"[{reason}] {detail}")

    @property
    def retryable(self) -> bool:
        return self.reason in self.RETRYABLE_REASONS


@dataclass
class FriendbotFundingResult:
    """Outcome of a Friendbot funding attempt."""

    address: str
    funded: bool
    # "funded"          -- Friendbot created/topped up the account
    # "already_funded"  -- account already exists on the ledger
    # "skipped"         -- balance above 0, no request was made
    outcome: str
    requested_at: datetime
    status_code: Optional[int] = None
    transaction_hash: Optional[str] = None
    ledger: Optional[int] = None
    balance_before: Optional[Decimal] = None
    detail: Optional[str] = None
    response: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe summary suitable for logs, audit records and API payloads."""
        return {
            "address": self.address,
            "funded": self.funded,
            "outcome": self.outcome,
            "requested_at": self.requested_at.isoformat(),
            "status_code": self.status_code,
            "transaction_hash": self.transaction_hash,
            "ledger": self.ledger,
            "balance_before": (
                str(self.balance_before) if self.balance_before is not None else None
            ),
            "detail": self.detail,
        }


class FriendbotService:
    """Requests testnet XLM from Friendbot for operator wallets."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or app_settings

    # ── Guards ───────────────────────────────────────────────────────────

    def _assert_available(self) -> None:
        if not self._settings.STELLAR_FRIENDBOT_ENABLED:
            raise FriendbotError(
                FriendbotError.REASON_DISABLED,
                "Friendbot funding is disabled (STELLAR_FRIENDBOT_ENABLED=false).",
            )
        if not self._settings.supports_friendbot:
            raise FriendbotError(
                FriendbotError.REASON_UNSUPPORTED_NETWORK,
                f"Friendbot is not available on network "
                f"'{self._settings.STELLAR_NETWORK}'.",
            )

    @staticmethod
    def _normalize_address(address: str) -> str:
        try:
            return str(normalize(address))
        except WalletAddressError as exc:
            raise FriendbotError(
                FriendbotError.REASON_INVALID_ADDRESS, exc.reason
            ) from exc

    # ── Balance read ─────────────────────────────────────────────────────

    async def get_native_balance(self, address: str) -> Decimal:
        """Return the account's XLM balance, or 0 when it is not on the ledger.

        A missing account (Horizon 404) is reported as a 0 balance because
        that is exactly the state a testnet reset leaves behind.
        """
        normalized = self._normalize_address(address)
        url = f"{self._settings.horizon_url}/accounts/{normalized}"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url)
                resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return ACCOUNT_NOT_FOUND_BALANCE
            raise FriendbotError(
                FriendbotError.REASON_REQUEST_FAILED,
                f"Horizon returned {exc.response.status_code} for {normalized}.",
            ) from exc
        except httpx.RequestError as exc:
            raise FriendbotError(
                FriendbotError.REASON_REQUEST_FAILED,
                f"Horizon unreachable while reading balance for {normalized}: {exc}",
            ) from exc

        for balance in data.get("balances", []):
            if balance.get("asset_type") == "native":
                try:
                    return Decimal(str(balance.get("balance", "0")))
                except (InvalidOperation, TypeError):
                    return ACCOUNT_NOT_FOUND_BALANCE
        return ACCOUNT_NOT_FOUND_BALANCE

    # ── Funding ──────────────────────────────────────────────────────────

    async def request_friendbot_funding(self, address: str) -> FriendbotFundingResult:
        """Request Friendbot funding for *address* and log the response.

        Raises:
            FriendbotError: when funding is disabled, the network has no
                faucet, the address is malformed, or Friendbot is
                unreachable. ``FriendbotError.retryable`` says whether a
                retry makes sense.
        """
        self._assert_available()
        normalized = self._normalize_address(address)
        requested_at = datetime.now(UTC)

        try:
            async with httpx.AsyncClient(
                timeout=self._settings.STELLAR_FRIENDBOT_TIMEOUT_SECONDS
            ) as client:
                resp = await client.get(
                    self._settings.friendbot_url, params={"addr": normalized}
                )
        except httpx.RequestError as exc:
            logger.error(
                "Friendbot request failed | address=%s network=%s error=%s",
                normalized,
                self._settings.STELLAR_NETWORK,
                exc,
            )
            raise FriendbotError(
                FriendbotError.REASON_REQUEST_FAILED,
                f"Friendbot unreachable for {normalized}: {exc}",
            ) from exc

        body = self._parse_body(resp)
        result = self._build_result(normalized, resp.status_code, body, requested_at)
        self._log_response(result, body)

        if not result.funded and result.outcome != "already_funded":
            raise FriendbotError(
                FriendbotError.REASON_REQUEST_FAILED,
                f"Friendbot returned {resp.status_code} for {normalized}: "
                f"{result.detail or 'no detail'}",
            )
        return result

    async def fund_if_unfunded(self, address: str) -> FriendbotFundingResult:
        """Fund *address* through Friendbot only when its XLM balance is 0.

        This is the auto-trigger: a wallet wiped by a testnet reset (or one
        that was never created) reads as 0 XLM and is re-funded without
        operator involvement. Any positive balance is left alone.
        """
        self._assert_available()
        normalized = self._normalize_address(address)
        balance = await self.get_native_balance(normalized)

        if balance > 0:
            logger.debug(
                "Friendbot funding skipped | address=%s balance=%s XLM",
                normalized,
                balance,
            )
            return FriendbotFundingResult(
                address=normalized,
                funded=False,
                outcome="skipped",
                requested_at=datetime.now(UTC),
                balance_before=balance,
                detail="Balance above zero; no funding required.",
            )

        logger.info(
            "Zero XLM balance detected — auto-triggering Friendbot | address=%s",
            normalized,
        )
        result = await self.request_friendbot_funding(normalized)
        result.balance_before = balance
        return result

    # ── Response handling ────────────────────────────────────────────────

    @staticmethod
    def _parse_body(resp: httpx.Response) -> dict[str, Any]:
        try:
            body = resp.json()
        except ValueError:
            return {"raw": resp.text[:500]}
        return body if isinstance(body, dict) else {"raw": body}

    @staticmethod
    def _extract_result_codes(body: dict[str, Any]) -> list[str]:
        extras = body.get("extras") or {}
        codes = extras.get("result_codes") or {}
        operations = codes.get("operations") or []
        transaction = codes.get("transaction")
        return [c for c in ([transaction] if transaction else []) + list(operations) if c]

    @classmethod
    def _build_result(
        cls,
        address: str,
        status_code: int,
        body: dict[str, Any],
        requested_at: datetime,
    ) -> FriendbotFundingResult:
        if 200 <= status_code < 300:
            return FriendbotFundingResult(
                address=address,
                funded=True,
                outcome="funded",
                requested_at=requested_at,
                status_code=status_code,
                transaction_hash=body.get("hash") or body.get("id"),
                ledger=body.get("ledger"),
                detail=body.get("title"),
                response=body,
            )

        # Friendbot answers 400 with op_already_exists when the account is
        # already on the ledger — the wallet is usable, so this is a success
        # for our purposes rather than a failure to retry.
        if "op_already_exists" in cls._extract_result_codes(body):
            return FriendbotFundingResult(
                address=address,
                funded=False,
                outcome="already_funded",
                requested_at=requested_at,
                status_code=status_code,
                detail="Account already exists on the ledger.",
                response=body,
            )

        return FriendbotFundingResult(
            address=address,
            funded=False,
            outcome="failed",
            requested_at=requested_at,
            status_code=status_code,
            detail=body.get("detail") or body.get("title") or body.get("raw"),
            response=body,
        )

    @staticmethod
    def _log_response(result: FriendbotFundingResult, body: dict[str, Any]) -> None:
        """Log the Friendbot response — summary at INFO, full body at DEBUG."""
        if result.outcome == "failed":
            logger.error("Friendbot funding failed | %s", result.as_dict())
        else:
            logger.info("Friendbot funding response | %s", result.as_dict())
        logger.debug("Friendbot raw response | address=%s body=%s", result.address, body)


# Module-level singleton — stateless, safe to share across requests.
friendbot_service = FriendbotService()


def request_friendbot_funding(address: str) -> FriendbotFundingResult:
    """Blocking wrapper around :meth:`FriendbotService.request_friendbot_funding`."""
    import asyncio

    return asyncio.run(friendbot_service.request_friendbot_funding(address))


def fund_if_unfunded(address: str) -> FriendbotFundingResult:
    """Blocking wrapper around :meth:`FriendbotService.fund_if_unfunded`."""
    import asyncio

    return asyncio.run(friendbot_service.fund_if_unfunded(address))
