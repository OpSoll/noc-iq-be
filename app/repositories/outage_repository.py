from datetime import datetime
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.enums import OutageStatus, Severity
from app.models.orm.outage import OutageORM
from app.models.outage import Outage, Location, SLAStatus
from app.models.outage_dto import OutageCreate, OutageUpdate


def _orm_to_pydantic(orm: OutageORM) -> Outage:
    location = None
    if orm.location:
        location = Location(**orm.location)

    sla_status = None
    if orm.sla_status:
        sla_status = SLAStatus(**orm.sla_status)

    return Outage(
        id=orm.id,
        site_name=orm.site_name,
        site_id=orm.site_id,
        severity=orm.severity,
        status=orm.status,
        detected_at=orm.detected_at,
        resolved_at=orm.resolved_at,
        description=orm.description,
        affected_services=orm.affected_services or [],
        affected_subscribers=orm.affected_subscribers,
        assigned_to=orm.assigned_to,
        created_by=orm.created_by,
        location=location,
        sla_status=sla_status,
    )


class OutageRepository:
    def __init__(self, db: Session):
        self.db = db

    def list(
        self,
        severity: Optional[Severity] = None,
        status: Optional[OutageStatus] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        query = self.db.query(OutageORM)

        if severity:
            query = query.filter(OutageORM.severity == severity.value)
        if status:
            query = query.filter(OutageORM.status == status.value)

        total = query.count()
        items = query.offset((page - 1) * page_size).limit(page_size).all()

        return {
            "items": [_orm_to_pydantic(o) for o in items],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    def list_all(self) -> List[Outage]:
        rows = self.db.query(OutageORM).all()
        return [_orm_to_pydantic(r) for r in rows]

    def get(self, outage_id: str) -> Optional[Outage]:
        row = self.db.query(OutageORM).filter(OutageORM.id == outage_id).first()
        if not row:
            return None
        return _orm_to_pydantic(row)

    def get_orm(self, outage_id: str) -> Optional[OutageORM]:
        return self.db.query(OutageORM).filter(OutageORM.id == outage_id).first()

    def create(self, payload: OutageCreate) -> Outage:
        location_data = payload.location.model_dump() if payload.location else None
        orm = OutageORM(
            id=payload.id,
            site_name=payload.site_name,
            site_id=payload.site_id,
            severity=payload.severity.value,
            status=payload.status.value,
            detected_at=payload.detected_at,
            description=payload.description,
            affected_services=payload.affected_services,
            affected_subscribers=payload.affected_subscribers,
            assigned_to=payload.assigned_to,
            created_by=payload.created_by,
            location=location_data,
        )
        self.db.add(orm)
        self.db.commit()
        self.db.refresh(orm)
        return _orm_to_pydantic(orm)

    def bulk_create(self, outages: List[OutageCreate]) -> List[Outage]:
        created = []
        for payload in outages:
            location_data = payload.location.model_dump() if payload.location else None
            orm = OutageORM(
                id=payload.id,
                site_name=payload.site_name,
                site_id=payload.site_id,
                severity=payload.severity.value,
                status=payload.status.value,
                detected_at=payload.detected_at,
                description=payload.description,
                affected_services=payload.affected_services,
                affected_subscribers=payload.affected_subscribers,
                assigned_to=payload.assigned_to,
                created_by=payload.created_by,
                location=location_data,
            )
            self.db.add(orm)
            created.append(orm)
        self.db.commit()
        for orm in created:
            self.db.refresh(orm)
        return [_orm_to_pydantic(o) for o in created]

    def update(self, outage_id: str, payload: OutageUpdate) -> Optional[Outage]:
        orm = self.get_orm(outage_id)
        if not orm:
            return None

        update_data = payload.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            if key == "location" and value is not None:
                setattr(orm, key, value if isinstance(value, dict) else value.model_dump())
            elif hasattr(value, "value"):  # enum
                setattr(orm, key, value.value)
            else:
                setattr(orm, key, value)

        orm.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(orm)
        return _orm_to_pydantic(orm)

    def delete(self, outage_id: str) -> None:
        orm = self.get_orm(outage_id)
        if orm:
            self.db.delete(orm)
            self.db.commit()

    def resolve(self, outage_id: str, mttr_minutes: int) -> Optional[Outage]:
        orm = self.get_orm(outage_id)
        if not orm:
            return None

        orm.status = OutageStatus.resolved.value
        orm.mttr_minutes = mttr_minutes
        orm.resolved_at = datetime.utcnow()
        orm.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(orm)
        return _orm_to_pydantic(orm)

    def list_violations(self) -> List[dict]:
        from app.services.sla import SLACalculator

        rows = (
            self.db.query(OutageORM)
            .filter(OutageORM.status == OutageStatus.resolved.value)
            .all()
        )

        violations = []
        for orm in rows:
            if orm.mttr_minutes is None:
                continue
            sla = SLACalculator.calculate(
                outage_id=orm.id,
                severity=orm.severity,
                mttr_minutes=orm.mttr_minutes,
            )
            if sla["status"] == "violated":
                violations.append({"outage": _orm_to_pydantic(orm), "sla": sla})

        return violations
