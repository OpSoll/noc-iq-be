from typing import List

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.enums import OutageStatus, Severity
from app.models.outage import BulkOutageCreate, PaginatedOutages, ResolveOutageRequest
from app.models import Outage, OutageCreate, OutageUpdate
from app.repositories.outage_repository import OutageRepository
from app.repositories.sla_repository import SLARepository
from app.services.audit_log import audit_log
from app.services.sla import SLACalculator
from app.utils.exporter import export_outages

router = APIRouter()


@router.get("/export")
def export_outages_endpoint(format: str = "json", db: Session = Depends(get_db)):
    repo = OutageRepository(db)
    data = repo.list_all()
    exported = export_outages(data, format)

    if format == "csv":
        return Response(
            content=exported,
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=outages.csv"},
        )
    return exported


@router.get("/violations")
def list_violations(db: Session = Depends(get_db)):
    repo = OutageRepository(db)
    return repo.list_violations()


@router.get("/", response_model=PaginatedOutages)
def list_outages(
    severity: Severity | None = None,
    status: OutageStatus | None = None,
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
):
    repo = OutageRepository(db)
    return repo.list(severity, status, page, page_size)


@router.get("/{outage_id}", response_model=Outage)
def get_outage(outage_id: str, db: Session = Depends(get_db)):
    repo = OutageRepository(db)
    outage = repo.get(outage_id)
    if not outage:
        raise HTTPException(status_code=404, detail="Outage not found")
    return outage


@router.post("/", response_model=Outage)
def create_outage(payload: OutageCreate, db: Session = Depends(get_db)):
    repo = OutageRepository(db)
    outage = repo.create(payload)
    audit_log.log("outage_created", {"id": outage.id})
    return outage


@router.post("/bulk", response_model=dict)
def bulk_create_outages(payload: BulkOutageCreate, db: Session = Depends(get_db)):
    repo = OutageRepository(db)
    created = repo.bulk_create(payload.outages)
    return {"count": len(created), "items": created}


@router.put("/{outage_id}", response_model=Outage)
def update_outage(outage_id: str, payload: OutageUpdate, db: Session = Depends(get_db)):
    repo = OutageRepository(db)
    existing = repo.get(outage_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Outage not found")

    updated = repo.update(outage_id, payload)
    return updated


@router.delete("/{outage_id}")
def delete_outage(outage_id: str, db: Session = Depends(get_db)):
    repo = OutageRepository(db)
    existing = repo.get(outage_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Outage not found")

    repo.delete(outage_id)
    return {"message": "Outage deleted successfully"}


@router.post("/{outage_id}/resolve")
def resolve_outage(outage_id: str, payload: ResolveOutageRequest, db: Session = Depends(get_db)):
    repo = OutageRepository(db)
    outage = repo.resolve(outage_id, payload.mttr_minutes)
    if not outage:
        raise HTTPException(status_code=404, detail="Outage not found")

    audit_log.log("outage_resolved", {"id": outage.id, "mttr": payload.mttr_minutes})

    sla = SLACalculator.calculate(
        outage_id=outage.id,
        severity=outage.severity.value,
        mttr_minutes=payload.mttr_minutes,
    )

    sla_repo = SLARepository(db)
    sla_repo.create(sla)

    return {"outage": outage, "sla": sla}


@router.post("/{outage_id}/recompute-sla")
def recompute_sla(outage_id: str, db: Session = Depends(get_db)):
    repo = OutageRepository(db)
    outage = repo.get(outage_id)
    if not outage:
        raise HTTPException(status_code=404, detail="Outage not found")

    if outage.status != OutageStatus.resolved:
        raise HTTPException(status_code=400, detail="Outage not resolved yet")

    orm = repo.get_orm(outage_id)
    sla = SLACalculator.calculate(
        outage_id=outage.id,
        severity=outage.severity.value,
        mttr_minutes=orm.mttr_minutes,
    )

    sla_repo = SLARepository(db)
    sla_repo.create(sla)

    audit_log.log("sla_recomputed", {"id": outage.id})
    return sla
