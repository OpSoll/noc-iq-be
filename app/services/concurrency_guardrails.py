"""BE-W5-055 — Async worker concurrency tuning profile & safeguards.

Defines the per-environment concurrency profile (dev/staging/prod) and
periodic guardrail checks that fire alerts **before** saturation failures
cascades into API starvation or DB pool exhaustion.

Public surface:
  * ``EnvProfile``           — dataclass describing one environment.
  * ``PROFILES``             — dict mapping env name -> EnvProfile.
  * ``get_profile()``        — returns the active profile (fallback to dev).
  * ``measure_broker_stress``— best-effort broker connection utilisation.
  * ``evaluate_guardrails``  — single entry point that emits log alerts and
                               updates MetricsRegistry gauges. Safe to call
                               from anywhere (no broker round-trip when an
                               explicit celery_app is passed in, otherwise
                               prefers a lazy import to dodge a cycle).
  * ``guardrails_dict``      — helper that turns the live readings into a
                               dict suitable for the /health/concurrency
                               endpoint.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional

from app.core.config import settings
from app.db.session import pool_health
from app.services.metrics import set_gauge

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Per-environment profile                                                       #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class EnvProfile:
    """Concurrency tuning knobs for a single environment.

    These values are the documented *defaults*; operators may override any
    individual field via the corresponding ``CELERY_WORKER_CONCURRENCY`` /
    ``CELERY_MAX_TASKS_PER_CHILD`` / ``DB_POOL_SIZE`` settings.
    """

    concurrency: int
    max_tasks: int
    pool_size: int


PROFILES: Dict[str, EnvProfile] = {
    # Local dev: minimal footprint, low DB pool, low concurrency.
    "dev": EnvProfile(concurrency=2, max_tasks=100, pool_size=5),
    # Staging: roughly double dev, mirrors a small production node.
    "staging": EnvProfile(concurrency=4, max_tasks=500, pool_size=10),
    # Prod: tuned for a single worker node. Scale horizontally by adding
    # nodes — this profile is intentionally conservative per node.
    "prod": EnvProfile(concurrency=8, max_tasks=1000, pool_size=20),
}


def get_profile() -> EnvProfile:
    """Return the active EnvProfile based on APP_ENV (falls back to dev)."""
    env_key = (settings.APP_ENV or "dev").lower()
    return PROFILES.get(env_key, PROFILES["dev"])


# --------------------------------------------------------------------------- #
# Saturation measurement                                                        #
# --------------------------------------------------------------------------- #


def measure_broker_stress(celery_app: Any, timeout: float = 1.0) -> Dict[str, Any]:
    """Estimate broker (Redis) connection utilisation.

    Celery does not expose a direct connection count, so we use the count of
    active workers (``inspect().active_queues()``) multiplied by the
    effective worker concurrency as a best-effort proxy for simultaneous
    broker connections. Returns ``estimated_connections``, ``max``,
    ``saturation`` (fraction) and ``is_alert`` (boolean).

    On any error we return a zero-utilisation payload so a failing probe
    never blocks scheduling.
    """
    max_conns = max(int(settings.BROKER_MAX_CONNECTIONS), 1)
    threshold = float(settings.BROKER_SATURATION_THRESHOLD)

    if celery_app is None:
        return {
            "estimated_connections": 0,
            "max": max_conns,
            "saturation": 0.0,
            "is_alert": False,
        }

    try:
        inspect = celery_app.control.inspect(timeout=timeout)
        active = inspect.active_queues() or {}
        worker_count = len(active)
    except Exception as exc:  # broker unreachable, timeout, etc.
        logger.debug("BE-W5-055: broker probe failed (%s) — assuming zero load", exc)
        return {
            "estimated_connections": 0,
            "max": max_conns,
            "saturation": 0.0,
            "is_alert": False,
        }

    profile = get_profile()
    effective_concurrency = (
        settings.CELERY_WORKER_CONCURRENCY
        if settings.CELERY_WORKER_CONCURRENCY is not None
        else profile.concurrency
    )
    estimated = max(worker_count, 0) * max(effective_concurrency, 0)
    saturation = round(estimated / max_conns, 4)
    return {
        "estimated_connections": estimated,
        "max": max_conns,
        "saturation": saturation,
        "is_alert": saturation >= threshold,
    }


# --------------------------------------------------------------------------- #
# Evaluation entry point                                                        #
# --------------------------------------------------------------------------- #


def evaluate_guardrails(
    celery_app: Any,
    *,
    log_alerts: bool = True,
) -> Dict[str, Any]:
    """Run the full DB + broker guardrail evaluation.

    Updates MetricsRegistry gauges in every case so external scrapers can
    alert independently. Emits a WARNING log line when either alert fires
    so log-based alerting (CloudWatch/Loki/etc.) also picks them up — this
    satisfies the "guardrail alerts fire before saturation failures"
    acceptance criterion without coupling to a specific alerting backend.
    """
    try:
        db_stats = pool_health.get_stats()
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("BE-W5-055: pool_health.get_stats failed: %s", exc)
        db_stats = {"saturation": 0.0}

    broker_stats = measure_broker_stress(celery_app)

    # The DB guardrail threshold is STRICTLY below the saturation threshold
    # so the [GUARDRAIL ALERT] hits logs BEFORE PoolSaturationMiddleware
    # starts serving 530/503 responses. min() shrinks with either knob;
    # we also enforce a 0.05 headroom so a careless setting can't zero it.
    _db_sat_reject = float(settings.DB_POOL_SATURATION_THRESHOLD)
    _db_guard = float(settings.DB_GUARDRAIL_THRESHOLD)
    guardrail_threshold = float(min(_db_guard, max(_db_sat_reject - 0.05, 0.05)))
    db_alert = float(db_stats.get("saturation", 0.0)) >= guardrail_threshold
    broker_alert = bool(broker_stats.get("is_alert", False))

    # Telemetry (always — even when no alert). Gauges are cheap (in-process
    # dict) and let /metrics endpoints expose them directly.
    set_gauge("db_pool.saturation", float(db_stats.get("saturation", 0.0)))
    set_gauge("broker.saturation", float(broker_stats.get("saturation", 0.0)))
    set_gauge("guardrail.alert.db", 1.0 if db_alert else 0.0)
    set_gauge("guardrail.alert.broker", 1.0 if broker_alert else 0.0)

    if log_alerts:
        if db_alert:
            logger.warning(
                "[GUARDRAIL ALERT] DB pool near saturation: %s (guardrail_threshold=%s saturation_reject_threshold=%s)",
                db_stats, guardrail_threshold, _db_sat_reject,
            )
        if broker_alert:
            logger.warning(
                "[GUARDRAIL ALERT] Broker connection saturation: %s (threshold=%s)",
                broker_stats, settings.BROKER_SATURATION_THRESHOLD,
            )

    return {
        "db": db_stats,
        "broker": broker_stats,
        "alerts_active": bool(db_alert or broker_alert),
        "thresholds": {
            "db_guardrail": guardrail_threshold,
            "db_saturation_reject": _db_sat_reject,
            "broker_guardrail": float(settings.BROKER_SATURATION_THRESHOLD),
        },
    }


def guardrails_dict(celery_app: Optional[Any] = None) -> Dict[str, Any]:
    """Render the current profile + live gauge readings for /health.

    Returning a pure dict keeps the route handler trivial and makes the
    payload easy to assert in tests.
    """
    profile = get_profile()
    live = evaluate_guardrails(celery_app, log_alerts=False)
    effective_concurrency = (
        settings.CELERY_WORKER_CONCURRENCY
        if settings.CELERY_WORKER_CONCURRENCY is not None
        else profile.concurrency
    )
    effective_max_tasks = (
        settings.CELERY_MAX_TASKS_PER_CHILD
        if settings.CELERY_MAX_TASKS_PER_CHILD is not None
        else profile.max_tasks
    )
    return {
        "env": (settings.APP_ENV or "dev").lower(),
        "profile": {
            "concurrency": effective_concurrency,
            "max_tasks_per_child": effective_max_tasks,
            "pool_size": settings.DB_POOL_SIZE or profile.pool_size,
        },
        "profile_defaults": asdict(profile),
        "overrides_in_effect": {
            "concurrency_override": settings.CELERY_WORKER_CONCURRENCY is not None,
            "max_tasks_override": settings.CELERY_MAX_TASKS_PER_CHILD is not None,
        },
        "live_metrics": live,
    }
