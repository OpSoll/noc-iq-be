from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.orm.sla import SLAResultORM
from app.models.sla import SLAResult


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
