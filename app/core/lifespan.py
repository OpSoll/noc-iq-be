from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator
import httpx
from fastapi import FastAPI

from app.core.config import settings
from app.db.session import shutdown_db_pool, warmup_db_pool
from app.core.tracing import init_tracing, shutdown_tracing, instrument_fastapi

logger = logging.getLogger(__name__)

# Optional Redis import – graceful fallback if redis is unavailable.
try:
    import redis.asyncio as aioredis  # type: ignore[import-untyped]

    _redis_client: aioredis.Redis | None = None  # type: ignore[type-arg]
except ImportError:
    aioredis = None  # type: ignore[assignment]
    _redis_client = None


async def _startup_redis() -> None:
    """Create a Redis connection and ping to verify connectivity."""
    global _redis_client
    if aioredis is None:
        logger.info("redis package not installed – skipping Redis startup")
        return
    try:
        _redis_client = aioredis.from_url(
            "redis://localhost:6379", decode_responses=True
        )
        await _redis_client.ping()
        logger.info("Redis connection established")
    except Exception:
        logger.exception("Failed to connect to Redis during startup")
        _redis_client = None


async def _shutdown_redis() -> None:
    """Close Redis connections."""
    global _redis_client
    if _redis_client is not None:
        try:
            await _redis_client.aclose()
            logger.info("Redis connection closed")
        except Exception:
            logger.exception("Error closing Redis connection")
        _redis_client = None


def _check_celery() -> None:
    """Verify that Celery is reachable (inspect active workers)."""
    try:
        from app.tasks.celery_app import celery_app  # type: ignore[import-untyped]

        inspect = celery_app.control.inspect(timeout=5.0)
        active = inspect.active() or {}
        logger.info("Celery workers online: %d", len(active))
    except ImportError:
        logger.info("Celery not configured – skipping connection check")
    except (Exception, BaseException) as exc:
        logger.warning("Celery connection check skipped: %s", exc)


async def _check_stellar_network() -> None:
    """Verify that the configured Stellar network matches the Horizon server's network."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(settings.horizon_url)
            resp.raise_for_status()
            horizon_data = resp.json()
            horizon_network = horizon_data.get("network_passphrase")

        if not horizon_network:
            logger.warning("Could not determine Stellar network from Horizon server.")
            return

        if horizon_network != settings.STELLAR_NETWORK:
            logger.error(
                "Stellar network mismatch: HORIZON_URL is for '%s' but STELLAR_NETWORK is set to '%s'",
                horizon_network,
                settings.STELLAR_NETWORK,
            )
            raise RuntimeError("Stellar network mismatch")
        else:
            logger.info("Stellar network check passed: %s", settings.STELLAR_NETWORK)

    except httpx.RequestError as exc:
        logger.warning("Could not connect to Horizon server for network check: %s", exc)
    except Exception as exc:
        logger.warning("Stellar network check skipped: %s", exc)


def _verify_sla_contract_abi() -> None:
    """Fail startup if the Soroban SLA ABI cannot match local calculations."""
    if settings.CONTRACT_EXECUTION_MODE != "soroban_rpc":
        return
    if not settings.SLA_CONTRACT_SPEC_PATH:
        from app.services.sla.contract_spec import ContractSpecMismatchError

        raise ContractSpecMismatchError(
            "SLA_CONTRACT_SPEC_PATH is required when CONTRACT_EXECUTION_MODE=soroban_rpc."
        )
    from app.services.sla.contract_spec import verify_sla_contract_spec_file

    verify_sla_contract_spec_file(settings.SLA_CONTRACT_SPEC_PATH)


@asynccontextmanager
async def app_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # --- startup ---
    logger.info("Application starting up")
    init_tracing()
    instrument_fastapi(app)
    warmup_db_pool()
    await _startup_redis()
    _check_celery()
    _verify_sla_contract_abi()
    await _check_stellar_network()

    yield

    # --- shutdown ---
    logger.info("Application shutting down")
    await _shutdown_redis()
    shutdown_db_pool()
    shutdown_tracing()