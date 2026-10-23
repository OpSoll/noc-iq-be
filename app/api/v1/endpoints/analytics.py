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
        class SeverityViolationStats(BaseModel):
    severity: str = Field(..., description="Severity tier (e.g., P1, P2, P3, P4)")
    total_incidents: int = Field(..., description="Total incidents in this severity tier")
    violation_count: int = Field(..., description="Number of SLA violations in this severity tier")
    violation_rate: float = Field(..., description="Percentage of SLA violations for this tier")

class SLAViolationAnalyticsResponse(BaseModel):
    total_incidents: int = Field(..., description="Total incidents across all tiers")
    total_violations: int = Field(..., description="Total SLA violations across all tiers")
    overall_violation_rate: float = Field(..., description="Overall SLA violation rate percentage")
    by_severity: Optional[List[SeverityViolationStats]] = Field(None, description="Violation rates grouped by severity tier")

def calculate_sla_violation_rate(
    incidents: List[Dict[str, any]], 
    group_by_severity: bool = False
) -> SLAViolationAnalyticsResponse:
    """
    Calculates SLA violation rates with optional severity grouping.
    Omits empty severity tiers and preserves accurate overall totals. (#923)
    """
    total_incidents = len(incidents)
    total_violations = sum(1 for inc in incidents if inc.get("violation", False))
    overall_rate = (total_violations / total_incidents * 100.0) if total_incidents > 0 else 0.0

    by_severity_result: Optional[List[SeverityViolationStats]] = None

    if group_by_severity:
        severity_map: Dict[str, Dict[str, int]] = {}
        
        for inc in incidents:
            sev = inc.get("severity", "UNKNOWN")
            if sev not in severity_map:
                severity_map[sev] = {"total": 0, "violations": 0}
            
            severity_map[sev]["total"] += 1
            if inc.get("violation", False):
                severity_map[sev]["violations"] += 1

        by_severity_list = []
        for sev, stats in severity_map.items():
            if stats["total"] > 0:
                rate = (stats["violations"] / stats["total"]) * 100.0
                by_severity_list.append(
                    SeverityViolationStats(
                        severity=sev,
                        total_incidents=stats["total"],
                        violation_count=stats["violations"],
                        violation_rate=round(rate, 2)
                    )
                )
        
        if by_severity_list:
            by_severity_result = by_severity_list

    return SLAViolationAnalyticsResponse(
        total_incidents=total_incidents,
        total_violations=total_violations,
        overall_violation_rate=round(overall_rate, 2),
        by_severity=by_severity_result
    )
    def get_bucket_key(timestamp: datetime, interval: str) -> str:
    """
    Returns the time bucket key ('daily', 'weekly', 'monthly') for a given timestamp. (#924)
    """
    if interval == 'daily':
        return timestamp.strftime('%Y-%m-%d')
    elif interval == 'weekly':
        # ISO week start (Monday)
        start_of_week = timestamp - timedelta(days=timestamp.weekday())
        return start_of_week.strftime('%Y-%m-%d')
    elif interval == 'monthly':
        return timestamp.strftime('%Y-%m')
    else:
        raise ValueError(f"Unsupported interval: {interval}")
