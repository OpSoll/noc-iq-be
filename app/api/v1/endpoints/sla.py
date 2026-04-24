from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.sla import (
    SLAConfigUpdateRequest,
    SLADashboardKPI,
    SLAPerformanceAggregation,
    SLAPreviewRequest,
    SLASeverityConfig,
    SLATrendPoint,
    SLAAnalyticsSnapshot,
)
from app.repositories.sla_repository import VALID_BUCKETS, SLARepository
from app.services.sla import SLACalculator
from app.services.sla.config import get_all_config, get_config_for_severity, update_config_for_severity
from app.models import SLAResult
from app.utils.cache import TTLCache

router = APIRouter()

# Cache dashboard KPIs and trends for 30 seconds to reduce repeated DB load.
_dashboard_cache: TTLCache = TTLCache(ttl_seconds=30)


def _invalidate_analytics_cache() -> None:
    """Invalidate all analytics cache keys after a mutating write (#157)."""
    _dashboard_cache.invalidate("dashboard_kpis")
    # Invalidate all trend keys by clearing the entire store
    _dashboard_cache.invalidate_prefix("trends_")


@router.get("/calculate", response_model=SLAResult)
def calculate_sla(outage_id: str, severity: str, mttr_minutes: int):
    try:
        return SLACalculator.calculate(
            outage_id=outage_id,
            severity=severity,
            mttr_minutes=mttr_minutes,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/preview")
def preview_sla(payload: SLAPreviewRequest):
    result = calculate_sla(
        outage_id="PREVIEW",
        severity=payload.severity.value,
        mttr_minutes=payload.mttr_minutes,
    )
    return result


@router.get("/config", response_model=dict[str, SLASeverityConfig])
def get_sla_config():
    return get_all_config()


@router.get("/config/{severity}", response_model=SLASeverityConfig)
def get_sla_config_by_severity(severity: str):
    try:
        return get_config_for_severity(severity)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/config/{severity}", response_model=SLASeverityConfig)
def update_sla_config(severity: str, payload: SLAConfigUpdateRequest):
    try:
        return update_config_for_severity(severity, payload)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/analytics/dashboard", response_model=SLADashboardKPI)
def get_sla_dashboard_kpis(
    severity: str | None = Query(default=None),
    site_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    cache_key = f"dashboard_kpis_{severity}_{site_id}"
    cached = _dashboard_cache.get(cache_key)
    if cached is not None:
        return cached
    repo = SLARepository(db)
    result = repo.aggregate_dashboard_kpis(severity=severity, site_id=site_id)
    _dashboard_cache.set(cache_key, result)
    return result


@router.get("/analytics/trends", response_model=list[SLATrendPoint])
def get_sla_trends(
    days: int = Query(default=7, ge=1, le=90),
    bucket: str = Query(default="day", description="Bucket interval: day, week, month"),
    tz: str = Query(default="UTC", description="IANA timezone name, e.g. America/New_York"),
    severity: str | None = Query(default=None),
    site_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    if bucket not in VALID_BUCKETS:
        raise HTTPException(status_code=400, detail=f"Invalid bucket '{bucket}'. Must be one of: {', '.join(VALID_BUCKETS)}")

    cache_key = f"trends_{days}_{bucket}_{tz}_{severity}_{site_id}"
    cached = _dashboard_cache.get(cache_key)
    if cached is not None:
        return cached

    repo = SLARepository(db)
    try:
        result = repo.aggregate_trends(limit_days=days, bucket=bucket, tz=tz, severity=severity, site_id=site_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    _dashboard_cache.set(cache_key, result)
    return result


@router.get("/performance/aggregation", response_model=SLAPerformanceAggregation)
def aggregate_sla_performance(
    start_date: datetime | None = Query(default=None),
    end_date: datetime | None = Query(default=None),
    severity: str | None = Query(default=None),
    site_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    if start_date and start_date.tzinfo is not None:
        start_date = start_date.astimezone(timezone.utc).replace(tzinfo=None)
    if end_date and end_date.tzinfo is not None:
        end_date = end_date.astimezone(timezone.utc).replace(tzinfo=None)

    if start_date and end_date and start_date > end_date:
        raise HTTPException(status_code=400, detail="start_date cannot be after end_date")

    repo = SLARepository(db)
    return repo.aggregate_performance(start_date=start_date, end_date=end_date, severity=severity, site_id=site_id)


@router.post("/analytics/snapshot", response_model=SLAAnalyticsSnapshot, status_code=201)
def create_analytics_snapshot(
    snapshot_key: str = Query(default="global"),
    db: Session = Depends(get_db),
):
    """Materialize current SLA aggregates into a persistent snapshot."""
    repo = SLARepository(db)
    snapshot = repo.create_snapshot(snapshot_key=snapshot_key)
    _invalidate_analytics_cache()
    return snapshot


@router.get("/analytics/snapshot", response_model=SLAAnalyticsSnapshot)
def get_latest_analytics_snapshot(
    snapshot_key: str = Query(default="global"),
    db: Session = Depends(get_db),
):
    """Return the most recent pre-aggregated analytics snapshot."""
    repo = SLARepository(db)
    snapshot = repo.get_latest_snapshot(snapshot_key=snapshot_key)
    if not snapshot:
        raise HTTPException(status_code=404, detail="No snapshot found for the given key")
    return snapshot
