"""V1 health endpoints (issue #536, #505).

Exposes ``GET /api/v1/health/detailed`` with database, Redis,
Celery worker heartbeat and Stellar operator wallet balance status.
Also exposes ``GET /api/v1/health/settings`` for admin-only runtime
configuration inspection (issue #994).
"""

import logging
from datetime import datetime
from typing import Any, Dict

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.config import settings
from app.core.security import require_admin
from app.db.session import engine
from app.services.stellar.balance_monitor import read_wallet_health
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


def _wallet_health() -> dict:
    """Return cached Stellar wallet balance metrics, never raising.

    Reads only the snapshot written by the balance monitor beat task — the
    health endpoint must not make a Horizon call on the request path.
    """
    try:
        return read_wallet_health()
    except Exception:
        logger.warning("Failed to read Stellar wallet balance health")
        return {"status": "unknown", "healthy": None, "error": "unavailable"}


@router.get("/health/detailed")
def detailed_health() -> JSONResponse:
    """Detailed health probe: database, Redis and Celery worker heartbeat.

    Returns HTTP 200 when all dependencies are healthy, HTTP 503 when any
    dependency is down.

    Issue #505: explicitly pings PostgreSQL (SELECT 1) and Redis (PING).
    Issue #536: includes Celery worker heartbeat status.
    Also reports Stellar operator wallet balance metrics under ``wallet``.
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
        # Operator wallet balance metrics, as recorded by the 15-minute
        # balance monitor beat. Reported for observability only: a low
        # balance is alerted on by the monitor and does not mark the API
        # itself unhealthy.
        "wallet": _wallet_health(),
    }
    return JSONResponse(status_code=200 if all_healthy else 503, content=payload)


# ---------------------------------------------------------------------------
# Issue #994: Settings snapshot endpoint for admins
# ---------------------------------------------------------------------------

# Secret setting names that must never be exposed in the snapshot.
_SECRET_SETTING_NAMES: set[str] = {
    "SECRET_KEY",
    "JWT_SECRET_KEY",
    "API_KEY",
    "STELLAR_POOL_SECRET_KEY",
    "STELLAR_OPERATOR_SECRET_ENCRYPTED",
    "STELLAR_KEY_ENCRYPTION_KEY",
    "PAYMENT_WEBHOOK_SECRET",
    "CONTRACT_CANONICAL_SALT",
    "WORKER_ALERT_WEBHOOK_URL",
    "WALLET_ALERT_WEBHOOK_URL",
}

# Property names that are computed (not env-set) and safe to expose.
_SAFE_PROPERTIES: set[str] = {"horizon_url", "friendbot_url", "supports_friendbot", "monitored_wallet_address"}


def _build_settings_snapshot() -> Dict[str, Any]:
    """Build a grouped, non-secret snapshot of runtime settings.

    Issue #994: admins need to inspect non-secret runtime configuration.
    Secret values are excluded entirely (never masked) so they cannot leak
    through logs, API responses, or downstream consumers.
    """
    all_fields = settings.model_fields.keys()

    # Collect all non-secret model field values.
    safe_values: Dict[str, Any] = {}
    for name in sorted(all_fields):
        if name in _SECRET_SETTING_NAMES:
            continue
        try:
            value = getattr(settings, name)
        except Exception:
            continue
        # Skip callables and dunder attributes.
        if callable(value):
            continue
        safe_values[name] = value

    # Add safe computed properties.
    for prop in sorted(_SAFE_PROPERTIES):
        try:
            safe_values[prop] = getattr(settings, prop)
        except Exception:
            pass

    # Group settings by logical section so the response is easy to navigate.
    _SECTION_PREFIXES: list[tuple[str, tuple[str, ...]]] = [
        ("celery", ("CELERY_",)),
        ("webhook", ("WEBHOOK_",)),
        ("wallet", ("WALLET_",)),
        ("payment", ("PAYMENT_",)),
        ("stellar", ("STELLAR_",)),
        ("bridge", ("BRIDGE_",)),
        ("contract", ("CONTRACT_", "ALLOWED_CONTRACT_")),
        ("job", ("JOB_",)),
        ("db", ("DB_", "DATABASE_")),
        ("auth", ("AUTH_",)),
        ("metrics", ("METRICS_", "OTEL_")),
        ("migration", ("MIGRATION_",)),
        ("rate_limit", ("RATE_LIMIT_",)),
        ("startup", ("STARTUP_",)),
    ]

    grouped: Dict[str, Any] = {}
    ungrouped: Dict[str, Any] = {}

    for key, value in sorted(safe_values.items()):
        placed = False
        for section, prefixes in _SECTION_PREFIXES:
            if any(key.startswith(prefix) for prefix in prefixes):
                grouped.setdefault(section, {})[key] = value
                placed = True
                break
        if not placed:
            ungrouped[key] = value

    if ungrouped:
        grouped["general"] = ungrouped

    # Sort each section for deterministic output.
    return {section: dict(sorted(items.items())) for section, items in grouped.items()}


@router.get("/health/settings")
def get_settings_snapshot(current_user=Depends(require_admin)) -> Dict[str, Any]:
    """Return a non-secret, grouped snapshot of runtime settings.

    Issue #994: admins cannot inspect non-secret runtime configuration.
    This endpoint returns all non-secret settings grouped by logical
    section (Celery, webhooks, payments, Stellar, etc.).

    **Admin-only.** Secret settings (SECRET_KEY, JWT_SECRET_KEY,
    API_KEY, encryption keys, signing secrets) are excluded entirely.

    Returns:
        A dictionary with section names as keys, each containing
        a sorted dictionary of setting name → value pairs.
    """
    return _build_settings_snapshot()
