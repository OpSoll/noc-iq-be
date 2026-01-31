from typing import List
from fastapi import APIRouter, HTTPException
from datetime import datetime
from fastapi import Response
from app.utils.exporter import export_outages

from app.models.outage import (
    ResolveOutageRequest,
    PaginatedOutages,
)
from app.models.enums import Severity, OutageStatus
from app.models import Outage, OutageCreate, OutageUpdate

from app.services.outage_store import outage_store
from app.services.sla import SLACalculator

router = APIRouter()


@router.get("/", response_model=PaginatedOutages)
def list_outages(
    severity: Severity | None = None,
    status: OutageStatus | None = None,
    page: int = 1,
    page_size: int = 20,
):
    return outage_store.list(severity, status, page, page_size)




@router.get("/{outage_id}", response_model=Outage)
def get_outage(outage_id: str):
    outage = outage_store.get(outage_id)
    if not outage:
        raise HTTPException(status_code=404, detail="Outage not found")
    return outage


@router.post("/", response_model=Outage)
def create_outage(payload: OutageCreate):
    outage = Outage(
        **payload.model_dump(),
        resolved_at=None,
        sla_status=None,
    )
    return outage_store.create(outage)


@router.put("/{outage_id}", response_model=Outage)
def update_outage(outage_id: str, payload: OutageUpdate):
    existing = outage_store.get(outage_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Outage not found")

    updated_data = existing.model_dump()
    for key, value in payload.model_dump(exclude_unset=True).items():
        updated_data[key] = value

    updated_outage = Outage(**updated_data)
    outage_store.update(outage_id, updated_outage)
    return updated_outage


@router.delete("/{outage_id}")
def delete_outage(outage_id: str):
    existing = outage_store.get(outage_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Outage not found")

    outage_store.delete(outage_id)
    return {"message": "Outage deleted successfully"}


@router.post("/{outage_id}/resolve")
def resolve_outage(outage_id: str, payload: ResolveOutageRequest):
    outage = outage_store.resolve(outage_id, payload.mttr_minutes)
    if not outage:
        raise HTTPException(status_code=404, detail="Outage not found")

    sla = SLACalculator.calculate(
        outage_id=outage.id,
        severity=outage.severity.value,
        mttr_minutes=payload.mttr_minutes,
    )

    return {
        "outage": outage,
        "sla": sla,
    }


@router.post("/{outage_id}/recompute-sla")
def recompute_sla(outage_id: str):
    outage = outage_store.get(outage_id)
    if not outage:
        raise HTTPException(status_code=404, detail="Outage not found")

    if outage.status != OutageStatus.resolved:
        raise HTTPException(status_code=400, detail="Outage not resolved yet")

    sla = SLACalculator.calculate(
        outage_id=outage.id,
        severity=outage.severity.value,
        mttr_minutes=outage.mttr_minutes,
    )

    return sla

    @router.get("/export")
def export_outages_endpoint(format: str = "json"):
    data = outage_store.list_all()

    exported = export_outages(data, format)

    if format == "csv":
        return Response(
            content=exported,
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=outages.csv"},
        )

    return exported


@router.get("/violations")
def list_violations():
    return outage_store.list_violations()
