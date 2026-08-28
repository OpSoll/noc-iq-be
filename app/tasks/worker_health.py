"""Celery worker heartbeat monitor (issue #536).

A periodic beat task (``ping_celery_workers``, every 60 seconds) pings the
Celery worker fleet and persists the resulting health status to Redis, so the
API can expose it on ``GET /api/v1/health/detailed``.  If no active worker
responds to the ping, an alert is raised via the application log and an
optional webhook (``WORKER_ALERT_WEBHOOK_URL``).
"""

import json
import logging
from datetime import datetime, timezone

from app.core.config import settings
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

WORKER_HEALTH_REDIS_KEY = "celery:worker:health"
# TTL larger than the 60s beat interval so a stale entry is detectable.
WORKER_HEALTH_TTL_SECONDS = 120

PING_TIMEOUT_SECONDS = 5.0


def _get_redis():
    """Return a sync Redis client for the configured REDIS_URL."""
    import redis

    return redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)


def check_worker_health() -> dict:
    """Ping the Celery worker fleet and return the current health status."""
    active_workers: list = []
    try:
        inspect = celery_app.control.inspect(timeout=PING_TIMEOUT_SECONDS)
        ping_result = inspect.ping(timeout=PING_TIMEOUT_SECONDS) or {}
        active_workers = sorted(ping_result.keys())
    except Exception:
        logger.exception("Failed to ping Celery workers")
        active_workers = []

    return {
        "status": "ok" if active_workers else "down",
        "healthy": bool(active_workers),
        "active_workers": active_workers,
        "worker_count": len(active_workers),
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def store_worker_health(status: dict) -> bool:
    """Persist the latest worker health status in Redis with a TTL."""
    try:
        _get_redis().set(
            WORKER_HEALTH_REDIS_KEY,
            json.dumps(status),
            ex=WORKER_HEALTH_TTL_SECONDS,
        )
        return True
    except Exception:
        logger.warning("Failed to persist Celery worker health to Redis")
        return False


def read_worker_health() -> dict:
    """Return the most recent worker health status recorded by the beat task."""
    try:
        raw = _get_redis().get(WORKER_HEALTH_REDIS_KEY)
        if raw:
            return json.loads(raw)
    except Exception:
        logger.warning("Failed to read Celery worker health from Redis")

    return {
        "status": "unknown",
        "healthy": None,
        "active_workers": [],
        "worker_count": 0,
        "checked_at": None,
    }


def _send_alert_webhook(status: dict) -> None:
    """POST a worker-down alert to ``WORKER_ALERT_WEBHOOK_URL`` if configured."""
    url = settings.WORKER_ALERT_WEBHOOK_URL
    if not url:
        return
    try:
        import httpx

        payload = {
            "event": "celery.workers_down",
            "message": "No active Celery workers responded to the heartbeat ping.",
            "details": status,
        }
        httpx.post(url, json=payload, timeout=10.0)
        logger.info("Worker-down alert webhook sent to %s", url)
    except Exception:
        logger.exception("Failed to send worker-down alert webhook to %s", url)


@celery_app.task(name="app.tasks.worker_health.ping_celery_workers")
def ping_celery_workers() -> dict:
    """Beat task: ping the worker fleet, persist status, alert when down."""
    status = check_worker_health()
    store_worker_health(status)

    if not status["healthy"]:
        logger.error(
            "ALERT: No active Celery workers responded to the heartbeat ping "
            "(checked_at=%s). Background job processing may be stalled.",
            status["checked_at"],
        )
        _send_alert_webhook(status)
    else:
        logger.info(
            "Celery worker heartbeat OK: %d worker(s) responded.",
            status["worker_count"],
        )

    return status
