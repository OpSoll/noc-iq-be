from datetime import datetime, timezone
from typing import List, Literal, Mapping, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import case, func, select, update
from sqlalchemy.orm import Session

from app.models.orm.outage import OutageORM
from app.models.orm.sla import SLAResultORM
from app.models.sla import SLAResult, SLADashboardKPI, SLAPerformanceAggregation, SLATrendPoint

BucketInterval = Literal["day", "week", "month"]
VALID_BUCKETS: tuple[str, ...] = ("day", "week", "month")


def _orm_to_pydantic(orm: SLAResultORM) -> SLAResult:
    return SLAResult(
        id=orm.id,
        outage_id=orm.outage_id,
        status=orm.status,
        mttr_minutes=orm.mttr_minutes,
        threshold_minutes=orm.threshold_minutes,
        amount=orm.amount,
        payment_type=orm.payment_type,
        rating=orm.rating,
    )


class SLARepository:
    def __init__(self, db: Session):
        self.db = db

    def create(self, sla_data: SLAResult | Mapping[str, object]) -> SLAResult:
        if isinstance(sla_data, SLAResult):
            payload = sla_data.model_dump()
        else:
            payload = dict(sla_data)

        # Demote any existing latest record for this outage (#154)
        self.db.execute(
            update(SLAResultORM)
            .where(SLAResultORM.outage_id == payload["outage_id"])
            .where(SLAResultORM.is_latest.is_(True))
            .values(is_latest=False)
        )

        orm = SLAResultORM(
            outage_id=payload["outage_id"],
            status=payload["status"],
            mttr_minutes=payload["mttr_minutes"],
            threshold_minutes=payload["threshold_minutes"],
            amount=payload["amount"],
            payment_type=payload["payment_type"],
            rating=payload["rating"],
            is_latest=True,
        )
        self.db.add(orm)
        self.db.commit()
        self.db.refresh(orm)
        return _orm_to_pydantic(orm)

    def create_if_changed(self, sla_data: SLAResult | Mapping[str, object]) -> SLAResult:
        if isinstance(sla_data, SLAResult):
            payload = sla_data.model_dump()
        else:
            payload = dict(sla_data)

        latest = self.get_by_outage(payload["outage_id"])
        if latest and latest.model_dump() == payload:
            return latest

        return self.create(payload)

    def get_by_outage(self, outage_id: str) -> Optional[SLAResult]:
        """Return the authoritative latest SLA result for an outage (#154)."""
        orm = (
            self.db.query(SLAResultORM)
            .filter(SLAResultORM.outage_id == outage_id, SLAResultORM.is_latest.is_(True))
            .first()
        )
        if not orm:
            # Fallback: no is_latest row yet (pre-migration data)
            orm = (
                self.db.query(SLAResultORM)
                .filter(SLAResultORM.outage_id == outage_id)
                .order_by(SLAResultORM.created_at.desc())
                .first()
            )
        if not orm:
            return None
        return _orm_to_pydantic(orm)

    def list_by_outage(self, outage_id: str) -> List[SLAResult]:
        rows = (
            self.db.query(SLAResultORM)
            .filter(SLAResultORM.outage_id == outage_id)
            .order_by(SLAResultORM.created_at.desc())
            .all()
        )
        return [_orm_to_pydantic(r) for r in rows]

    def aggregate_performance(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        severity: Optional[str] = None,
        site_id: Optional[str] = None,
    ) -> SLAPerformanceAggregation:
        latest_results_query = (
            select(
                SLAResultORM.outage_id.label("outage_id"),
                SLAResultORM.status.label("status"),
                SLAResultORM.mttr_minutes.label("mttr_minutes"),
                SLAResultORM.amount.label("amount"),
                func.row_number()
                .over(
                    partition_by=SLAResultORM.outage_id,
                    order_by=(SLAResultORM.created_at.desc(), SLAResultORM.id.desc()),
                )
                .label("rn"),
            )
            .join(OutageORM, OutageORM.id == SLAResultORM.outage_id)
        )

        if start_date:
            latest_results_query = latest_results_query.where(SLAResultORM.created_at >= start_date)
        if end_date:
            latest_results_query = latest_results_query.where(SLAResultORM.created_at <= end_date)
        if severity:
            latest_results_query = latest_results_query.where(OutageORM.severity == severity)
        if site_id:
            latest_results_query = latest_results_query.where(OutageORM.site_id == site_id)

        latest_results = latest_results_query.subquery()

        aggregate_query = (
            select(
                func.count(latest_results.c.outage_id).label("total_outages"),
                func.coalesce(
                    func.sum(case((latest_results.c.status == "violated", 1), else_=0)),
                    0,
                ).label("total_violations"),
                func.coalesce(func.avg(latest_results.c.mttr_minutes), 0.0).label("avg_mttr"),
                func.coalesce(func.sum(latest_results.c.amount), 0.0).label("payout_sum"),
            )
            .where(latest_results.c.rn == 1)
        )

        row = self.db.execute(aggregate_query).one()
        total_outages = int(row.total_outages or 0)
        total_violations = int(row.total_violations or 0)
        violation_rate = 0.0 if total_outages == 0 else total_violations / total_outages

        return SLAPerformanceAggregation(
            total_outages=total_outages,
            violation_rate=round(float(violation_rate), 4),
            avg_mttr=round(float(row.avg_mttr or 0.0), 2),
            payout_sum=round(float(row.payout_sum or 0.0), 2),
        )

    def aggregate_dashboard_kpis(
        self,
        severity: Optional[str] = None,
        site_id: Optional[str] = None,
    ) -> SLADashboardKPI:
        query = (
            select(
                func.count(SLAResultORM.id).label("total_outages"),
                func.coalesce(
                    func.sum(case((SLAResultORM.status == "violated", 1), else_=0)),
                    0,
                ).label("total_violations"),
                func.coalesce(
                    func.sum(case((SLAResultORM.payment_type == "reward", SLAResultORM.amount), else_=0.0)),
                    0.0,
                ).label("total_rewards"),
                func.coalesce(
                    func.sum(case((SLAResultORM.payment_type == "penalty", func.abs(SLAResultORM.amount)), else_=0.0)),
                    0.0,
                ).label("total_penalties"),
            )
        )

        if severity or site_id:
            query = query.join(OutageORM, OutageORM.id == SLAResultORM.outage_id)
            if severity:
                query = query.where(OutageORM.severity == severity)
            if site_id:
                query = query.where(OutageORM.site_id == site_id)

        row = self.db.execute(query).one()
        total_rewards = round(float(row.total_rewards or 0.0), 2)
        total_penalties = round(float(row.total_penalties or 0.0), 2)
        return SLADashboardKPI(
            total_outages=int(row.total_outages or 0),
            total_violations=int(row.total_violations or 0),
            total_rewards=total_rewards,
            total_penalties=total_penalties,
            net_payout=round(total_rewards - total_penalties, 2),
        )

    def aggregate_trends(
        self,
        limit_days: int = 7,
        bucket: BucketInterval = "day",
        tz: str = "UTC",
        severity: Optional[str] = None,
        site_id: Optional[str] = None,
    ) -> List[SLATrendPoint]:
        if bucket not in VALID_BUCKETS:
            raise ValueError(f"Invalid bucket '{bucket}'. Must be one of: {', '.join(VALID_BUCKETS)}")

        try:
            tzinfo = ZoneInfo(tz)
        except ZoneInfoNotFoundError:
            raise ValueError(f"Unknown timezone: '{tz}'")

        # Truncate created_at to the requested bucket in the target timezone.
        # We convert UTC → target tz using AT TIME ZONE (PostgreSQL).
        if bucket == "day":
            bucket_expr = func.date_trunc("day", func.timezone(tz, SLAResultORM.created_at))
        elif bucket == "week":
            bucket_expr = func.date_trunc("week", func.timezone(tz, SLAResultORM.created_at))
        else:  # month
            bucket_expr = func.date_trunc("month", func.timezone(tz, SLAResultORM.created_at))

        query = (
            select(
                bucket_expr.label("bucket"),
                func.count(SLAResultORM.id).label("total_outages"),
                func.coalesce(
                    func.sum(case((SLAResultORM.status == "violated", 1), else_=0)),
                    0,
                ).label("violations"),
                func.coalesce(
                    func.sum(case((SLAResultORM.payment_type == "reward", SLAResultORM.amount), else_=0.0)),
                    0.0,
                ).label("rewards"),
                func.coalesce(
                    func.sum(case((SLAResultORM.payment_type == "penalty", func.abs(SLAResultORM.amount)), else_=0.0)),
                    0.0,
                ).label("penalties"),
            )
            .group_by(bucket_expr)
            .order_by(bucket_expr.desc())
            .limit(limit_days)
        )

        if severity or site_id:
            query = query.join(OutageORM, OutageORM.id == SLAResultORM.outage_id)
            if severity:
                query = query.where(OutageORM.severity == severity)
            if site_id:
                query = query.where(OutageORM.site_id == site_id)

        rows = self.db.execute(query).all()

        return [
            SLATrendPoint(
                date=str(row.bucket),
                total_outages=int(row.total_outages or 0),
                violations=int(row.violations or 0),
                rewards=round(float(row.rewards or 0.0), 2),
                penalties=round(float(row.penalties or 0.0), 2),
            )
            for row in rows
        ][::-1]
