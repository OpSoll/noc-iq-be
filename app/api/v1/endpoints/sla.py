from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.sla import SLAPerformanceAggregation, SLAPreviewRequest
from app.repositories.sla_repository import SLARepository
from app.services.sla import SLACalculator
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
