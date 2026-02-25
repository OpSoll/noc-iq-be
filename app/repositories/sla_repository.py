from datetime import datetime
from typing import List, Optional

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models.orm.sla import SLAResultORM
from app.models.sla import SLAResult, SLAPerformanceAggregation


def _orm_to_pydantic(orm: SLAResultORM) -> SLAResult:
    return SLAResult(
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

    def create(self, sla_data: dict) -> SLAResult:
        orm = SLAResultORM(
            outage_id=sla_data["outage_id"],
            status=sla_data["status"],
            mttr_minutes=sla_data["mttr_minutes"],
            threshold_minutes=sla_data["threshold_minutes"],
            amount=sla_data["amount"],
            payment_type=sla_data["payment_type"],
            rating=sla_data["rating"],
        )
        self.db.add(orm)
        self.db.commit()
        self.db.refresh(orm)
        return _orm_to_pydantic(orm)

    def get_by_outage(self, outage_id: str) -> Optional[SLAResult]:
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
    ) -> SLAPerformanceAggregation:
        latest_results_query = select(
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

        if start_date:
            latest_results_query = latest_results_query.where(SLAResultORM.created_at >= start_date)
        if end_date:
            latest_results_query = latest_results_query.where(SLAResultORM.created_at <= end_date)

        latest_results = latest_results_query.subquery()

        aggregate_query = (
            select(
                func.count(latest_results.c.outage_id).label("total_outages"),
                func.coalesce(
                    func.sum(
                        case(
                            (latest_results.c.status == "violated", 1),
                            else_=0,
                        )
                    ),
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
