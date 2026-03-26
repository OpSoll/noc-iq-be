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
)
from app.repositories.sla_repository import SLARepository
from app.services.sla import SLACalculator
from app.services.sla.config import get_all_config, get_config_for_severity, update_config_for_severity
from app.models import SLAResult

router = APIRouter()


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
def get_sla_dashboard_kpis(db: Session = Depends(get_db)):
    repo = SLARepository(db)
    return repo.aggregate_dashboard_kpis()


@router.get("/analytics/trends", response_model=list[SLATrendPoint])
def get_sla_trends(
    days: int = Query(default=7, ge=1, le=90),
    db: Session = Depends(get_db),
):
    repo = SLARepository(db)
    return repo.aggregate_trends(limit_days=days)


@router.get("/performance/aggregation", response_model=SLAPerformanceAggregation)
def aggregate_sla_performance(
    start_date: datetime | None = Query(default=None),
    end_date: datetime | None = Query(default=None),
    db: Session = Depends(get_db),
):
    if start_date and start_date.tzinfo is not None:
        start_date = start_date.astimezone(timezone.utc).replace(tzinfo=None)
    if end_date and end_date.tzinfo is not None:
        end_date = end_date.astimezone(timezone.utc).replace(tzinfo=None)

    if start_date and end_date and start_date > end_date:
        raise HTTPException(status_code=400, detail="start_date cannot be after end_date")

    repo = SLARepository(db)
    return repo.aggregate_performance(start_date=start_date, end_date=end_date)
