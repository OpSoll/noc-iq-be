from fastapi import APIRouter, Response

from app.tasks.webhook_autoscaler import autoscaler
from app.db.session import pool_health
from app.core.rate_limiter import rate_limiter
from app.metrics.cardinality_guard import cardinality_guard

router = APIRouter()


@router.get("/metrics/webhook-workers")
def webhook_worker_metrics():
    return autoscaler.get_metrics()


@router.get("/health/pool")
def pool_health_stats():
    return pool_health.get_stats()


@router.get("/metrics/rate-limits")
def rate_limit_metrics():
    return rate_limiter.get_metrics()


@router.get("/metrics/cardinality")
def cardinality_metrics():
    return cardinality_guard.get_cardinality()
