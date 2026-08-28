"""V1 health endpoints (issue #536, #505).

Exposes ``GET /api/v1/health/detailed`` with database, Redis and
Celery worker heartbeat status.
"""

import logging
from datetime import datetime

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.config import settings
from app.db.session import engine
from app.tasks.worker_health import check_worker_health, read_worker_health

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


def _check_database() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            conn.commit()
        return True
    except Exception:
        return False


def _check_celery_broker() -> bool:
    try:
        from redis import Redis

        Redis.from_url(settings.CELERY_BROKER_URL).ping()
        return True
    except Exception:
        return False


def _check_redis() -> bool:
    """Ping the Redis instance configured via REDIS_URL.

    Issue #505: the detailed health endpoint must explicitly verify Redis
    connectivity independent of the Celery broker check.
    """
    try:
        from redis import Redis

        redis_url = getattr(settings, "REDIS_URL", None) or settings.CELERY_BROKER_URL
        Redis.from_url(redis_url).ping()
        return True
    except Exception:
        return False


@router.get("/health/detailed")
def detailed_health() -> JSONResponse:
    """Detailed health probe: database, Redis and Celery worker heartbeat.

    Returns HTTP 200 when all dependencies are healthy, HTTP 503 when any
    dependency is down.

    Issue #505: explicitly pings PostgreSQL (SELECT 1) and Redis (PING).
    Issue #536: includes Celery worker heartbeat status.
    """
    db_ok = _check_database()
    redis_ok = _check_redis()
    broker_ok = _check_celery_broker()

    workers = read_worker_health()
    if workers.get("status") == "unknown":
        workers = check_worker_health()

    all_healthy = db_ok and redis_ok and broker_ok and workers.get("status") != "down"
    status = "ok" if all_healthy else "degraded"

    payload = {
        "status": status,
        "timestamp": datetime.utcnow().isoformat(),
        "dependencies": {
            "database": "ok" if db_ok else "down",
            "redis": "ok" if redis_ok else "down",
            "celery_broker": "ok" if broker_ok else "down",
            "celery_workers": workers,
        },
    }
    return JSONResponse(status_code=200 if all_healthy else 503, content=payload)
