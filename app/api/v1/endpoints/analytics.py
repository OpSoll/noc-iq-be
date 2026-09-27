from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.security import require_engineer
from app.db.session import get_db
from app.models.sla import SLATrendPoint
from app.repositories.sla_repository import SLARepository

router = APIRouter()

# Issue #506: configurable grouping (daily, weekly, monthly) mapped to the
# repository's bucket intervals.
_GROUP_BY_TO_BUCKET = {
    "daily": "day",
    "weekly": "week",
    "monthly": "month",
}


@router.get(
    "/sla-trends",
    response_model=list[SLATrendPoint],
    summary="SLA trend buckets with configurable grouping",
)
def sla_trends(
    group_by: str = Query(
        default="daily",
        pattern="^(daily|weekly|monthly)$",
        description="Grouping interval: daily, weekly, or monthly",
    ),
    days: int = Query(default=30, ge=1, le=365, description="Number of buckets to return"),
    severity: str | None = Query(default=None),
    site_id: str | None = Query(default=None),
    current_user=Depends(require_engineer),
    db: Session = Depends(get_db),
):
    """Return timestamped uptime and breach metric buckets (Issue #506).

    Aggregates SLA results server-side with an optimized SQL query, so the
    frontend no longer has to aggregate raw outage entries client-side to
    render historical trend graphs.
    """
    bucket = _GROUP_BY_TO_BUCKET[group_by]
    repo = SLARepository(db)
    try:
        return repo.aggregate_trends(
            limit_days=days,
            bucket=bucket,
            tz="UTC",
            severity=severity,
            site_id=site_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
