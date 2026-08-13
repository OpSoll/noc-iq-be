from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI

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


@asynccontextmanager
async def app_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # --- startup ---
    logger.info("Application starting up")
    init_tracing()
    instrument_fastapi(app)
    warmup_db_pool()
    await _startup_redis()
    _check_celery()

    yield

    # --- shutdown ---
    logger.info("Application shutting down")
    await _shutdown_redis()
    shutdown_db_pool()
    shutdown_tracing()
