"""V1 health endpoints (issue #536).

Exposes ``GET /api/v1/health/detailed`` with database, Celery broker and
Celery worker heartbeat status.
"""

import logging
from datetime import datetime

from fastapi import APIRouter
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


@router.get("/health/detailed")
def detailed_health() -> dict:
    """Detailed health probe including Celery worker heartbeat status.

    Worker status is normally refreshed every 60 seconds by the
    ``ping_celery_workers`` beat task (issue #536); a live ping is used as a
    fallback when the beat task has not reported yet.
    """
    db_ok = _check_database()
    broker_ok = _check_celery_broker()

    workers = read_worker_health()
    if workers.get("status") == "unknown":
        workers = check_worker_health()

    status = "ok"
    if not db_ok or not broker_ok or workers.get("status") == "down":
        status = "degraded"

    return {
        "status": status,
        "timestamp": datetime.utcnow().isoformat(),
        "dependencies": {
            "database": "ok" if db_ok else "down",
            "celery_broker": "ok" if broker_ok else "down",
            "celery_workers": workers,
        },
    }
