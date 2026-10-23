"""Stellar payment services: fee bump wrapper and batch submission (issues #563, #565)."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Issue #560 — Deterministic payment idempotency key
# ---------------------------------------------------------------------------

def generate_payment_idempotency_key(
    outage_id: str, amount: float, recipient: str
) -> str:
    """Deterministically derive a Stellar payment idempotency key.

    Issue #560: ``sha256(outage_id:amount:recipient)`` so identical payouts
    (same outage, amount and recipient) always produce the same key. The key
    is stored in ``payment_transactions.idempotency_key`` (UNIQUE) and used
    to reject duplicate settlement attempts.

    Args:
        outage_id:  Identifier of the outage being settled.
        amount:     Payment amount (numeric, normalized by the caller).
        recipient:  Payee address / settlement wallet.

    Returns:
        Hex SHA-256 digest of the canonical ``outage_id:amount:recipient``
        seed string.
    """
    seed = f"{outage_id}:{amount}:{recipient}"
    return hashlib.sha256(seed.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Issue #563 — Fee bump transaction wrapper
# ---------------------------------------------------------------------------

@dataclass
class FeeBumpResult:
    """Result of a fee-bump transaction submission."""
    success: bool
    fee_bump_hash: Optional[str] = None
    inner_hash: Optional[str] = None
    error: Optional[str] = None
    raw_response: Optional[Dict[str, Any]] = None


def build_fee_bump_transaction(
    inner_xdr: str,
    sponsor_secret: str,
    base_fee: int = 200,
    network_passphrase: str = "Test SDF Network ; September 2015",
) -> str:
    """Wrap *inner_xdr* in a Stellar Fee Bump envelope signed by *sponsor_secret*.

    The sponsor account pays the network fee on behalf of the inner transaction
    source, enabling sponsored settlement payments.

    Args:
        inner_xdr:           Base64-encoded XDR of the signed inner transaction.
        sponsor_secret:      Stellar secret key of the fee-sponsor account.
        base_fee:            Fee per operation in stroops (default 200).
        network_passphrase:  Stellar network passphrase.

    Returns:
        Base64-encoded XDR of the signed Fee Bump transaction envelope.
    """
    try:
        from stellar_sdk import (  # type: ignore[import]
            Keypair,
            FeeBumpTransaction,
            TransactionEnvelope,
            Network,
        )

        sponsor_keypair = Keypair.from_secret(sponsor_secret)
        inner_envelope = TransactionEnvelope.from_xdr(inner_xdr, network_passphrase)

        fee_bump_tx = (
            FeeBumpTransaction.builder(
                fee_source=sponsor_keypair,
                base_fee=base_fee,
                inner_transaction_envelope=inner_envelope,
            )
            .build()
        )
        fee_bump_tx.sign(sponsor_keypair)
        return fee_bump_tx.to_xdr()

    except ImportError:
        # stellar_sdk not installed — return inner XDR unchanged in test/offline envs
        logger.warning(
            "stellar_sdk not available; returning inner XDR unchanged (non-production path)."
        )
        return inner_xdr


async def submit_fee_bump_transaction(
    inner_xdr: str,
    sponsor_secret: str,
    horizon_url: str,
    base_fee: int = 200,
    network_passphrase: str = "Test SDF Network ; September 2015",
) -> FeeBumpResult:
    """Build and submit a fee-bump transaction to Horizon.

    Args:
        inner_xdr:           Signed inner transaction XDR.
        sponsor_secret:      Secret key of the fee sponsor.
        horizon_url:         Horizon base URL.
        base_fee:            Fee per operation in stroops.
        network_passphrase:  Stellar network passphrase.

    Returns:
        :class:`FeeBumpResult` with ``success`` flag and hashes.
    """
    import httpx

    fee_bump_xdr = build_fee_bump_transaction(
        inner_xdr=inner_xdr,
        sponsor_secret=sponsor_secret,
        base_fee=base_fee,
        network_passphrase=network_passphrase,
    )

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{horizon_url.rstrip('/')}/transactions",
                data={"tx": fee_bump_xdr},
            )
            body = resp.json()

        if resp.status_code in (200, 201):
            return FeeBumpResult(
                success=True,
                fee_bump_hash=body.get("hash"),
                inner_hash=body.get("inner_transaction", {}).get("hash"),
                raw_response=body,
            )

        return FeeBumpResult(
            success=False,
            error=body.get("title", "Horizon submission failed"),
            raw_response=body,
        )

    except Exception as exc:
        logger.error("Fee bump submission failed: %s", exc)
        return FeeBumpResult(success=False, error=str(exc))


# ---------------------------------------------------------------------------
# Issue #565 — Batch payment submission queue with rate limiting (max 10 tx/s)
# ---------------------------------------------------------------------------

_BATCH_RATE_LIMIT = 10  # max transactions per second


@dataclass
class BatchSubmissionResult:
    """Aggregate result from a batch payment submission run."""
    submitted: int = 0
    succeeded: int = 0
    failed: int = 0
    throughput_tps: float = 0.0
    errors: List[Dict[str, Any]] = field(default_factory=list)


async def submit_batch_payments(
    payment_jobs: List[Dict[str, Any]],
    submit_fn: Callable[[Dict[str, Any]], Coroutine[Any, Any, Dict[str, Any]]],
    rate_limit: int = _BATCH_RATE_LIMIT,
) -> BatchSubmissionResult:
    """Submit *payment_jobs* sequentially with a token-bucket rate limiter.

    Throttles outgoing payment submissions to at most *rate_limit* transactions
    per second. Each job dict is passed to *submit_fn* which must return a dict
    with at least ``{"success": bool}``.

    Args:
        payment_jobs:  List of payment job dicts (arbitrary shape; passed to submit_fn).
        submit_fn:     Async callable that submits one payment and returns a result dict.
        rate_limit:    Maximum transactions per second (default 10).

    Returns:
        :class:`BatchSubmissionResult` with throughput and per-job error details.
    """
    if not payment_jobs:
        return BatchSubmissionResult()

    interval = 1.0 / rate_limit  # minimum seconds between submissions
    result = BatchSubmissionResult()
    start = time.monotonic()

    logger.info(
        "Starting batch payment submission: %d jobs at max %d tx/s",
        len(payment_jobs),
        rate_limit,
    )

    for i, job in enumerate(payment_jobs):
        job_start = time.monotonic()

        try:
            outcome = await submit_fn(job)
            result.submitted += 1
            if outcome.get("success"):
                result.succeeded += 1
            else:
                result.failed += 1
                result.errors.append({"job_index": i, "error": outcome.get("error"), "job": job})
        except Exception as exc:
            result.submitted += 1
            result.failed += 1
            result.errors.append({"job_index": i, "error": str(exc), "job": job})
            logger.error("Batch job %d failed: %s", i, exc)

        # Rate limiting: sleep for the remainder of the interval
        elapsed = time.monotonic() - job_start
        sleep_time = interval - elapsed
        if sleep_time > 0 and i < len(payment_jobs) - 1:
            await asyncio.sleep(sleep_time)

    total_elapsed = time.monotonic() - start
    result.throughput_tps = result.submitted / total_elapsed if total_elapsed > 0 else 0.0

    logger.info(
        "Batch complete: submitted=%d succeeded=%d failed=%d throughput=%.2f tx/s",
        result.submitted,
        result.succeeded,
        result.failed,
        result.throughput_tps,
    )
    return result
