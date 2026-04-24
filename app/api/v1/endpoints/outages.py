import csv
import io
import json
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import BulkOutageCreate, Outage, OutageCreate, OutageUpdate
from app.models.enums import OutageStatus, Severity
from app.models.outage import PaginatedOutages, ResolveOutageRequest
from app.models.outage_dto import OutageSortDirection, OutageSortField
from app.models.webhook import WebhookEvent
from app.repositories.outage_event_repository import OutageEventRepository
from app.repositories.outage_repository import OutageRepository
from app.repositories.payment_repository import PaymentRepository
from app.repositories.sla_repository import SLARepository
from app.services.audit_log import audit_log
from app.services.contracts import SLAContractAdapter, translate_contract_result
from app.services.webhook_service import trigger_sla_violation_webhooks
from app.utils.exporter import export_outages

router = APIRouter()


@router.get("/export")
def export_outages_endpoint(format: str = "json", db: Session = Depends(get_db)):
    repo = OutageRepository(db)
    data = repo.list_all()
    try:
        exported = export_outages(data, format)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

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
    search: str | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    page: int = 1,
    page_size: int = 20,
    sort_by: OutageSortField = Query(
        default=OutageSortField.detected_at,
        description="Supported sort fields: detected_at, site_name, severity, status, id",
    ),
    sort_direction: OutageSortDirection = Query(
        default=OutageSortDirection.desc,
        description="Sort direction: asc or desc. Default is desc.",
    ),
    db: Session = Depends(get_db),
):
    repo = OutageRepository(db)
    return repo.list(
        severity=severity,
        status=status,
        search=search,
        start_date=start_date,
        end_date=end_date,
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_direction=sort_direction,
    )


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
    try:
        outage = repo.create(payload)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit_log.log("outage_created", {"id": outage.id})
    OutageEventRepository(db).record(outage.id, "created", {"site_name": outage.site_name})
    return outage


@router.post("/bulk", response_model=dict)
def bulk_create_outages(payload: BulkOutageCreate, db: Session = Depends(get_db)):
    repo = OutageRepository(db)
    try:
        created = repo.bulk_create(payload.outages)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"count": len(created), "items": created}


@router.post("/import", response_model=dict, summary="Bulk import outages from CSV or JSON file")
async def import_outages(
    file: UploadFile = File(...),
    dry_run: bool = Query(default=False, description="Validate rows without writing to the database"),
    db: Session = Depends(get_db),
):
    content = await file.read()
    filename = file.filename or ""

    row_outcomes = []

    if filename.endswith(".json"):
        try:
            rows = json.loads(content)
            if not isinstance(rows, list):
                raise HTTPException(status_code=400, detail="JSON file must contain a list of outage objects")
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid JSON: {exc}") from exc
    elif filename.endswith(".csv"):
        try:
            reader = csv.DictReader(io.StringIO(content.decode("utf-8")))
            rows = list(reader)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid CSV: {exc}") from exc
    else:
        raise HTTPException(status_code=400, detail="Unsupported file format. Use .json or .csv")

    repo = OutageRepository(db)
    persisted_count = 0

    for i, row in enumerate(rows):
        try:
            payload = OutageCreate(**row)
            if dry_run:
                duplicate = repo.check_duplicate(payload)
                row_outcomes.append(
                    {
                        "row": i,
                        "id": payload.id,
                        "valid": True,
                        "duplicate": bool(duplicate),
                        "existing_id": duplicate.id if duplicate else None,
                    }
                )
            else:
                before = repo.get(payload.id)
                created = repo.create(payload)
                row_outcomes.append({"row": i, "id": payload.id, "valid": True, "persisted": True, "outage_id": created.id})
                if before is None and created.id == payload.id:
                    persisted_count += 1
        except Exception as exc:
            row_outcomes.append({"row": i, "valid": False, "error": str(exc)})

    return {
        "mode": "dry_run" if dry_run else "import",
        "total_rows": len(rows),
        "persisted": 0 if dry_run else persisted_count,
        "validated": sum(1 for row in row_outcomes if row.get("valid")),
        "errors": [row for row in row_outcomes if not row.get("valid")],
        "rows": row_outcomes,
    }


@router.put("/{outage_id}", response_model=Outage)
def update_outage(outage_id: str, payload: OutageUpdate, db: Session = Depends(get_db)):
    repo = OutageRepository(db)
    existing = repo.get(outage_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Outage not found")

    try:
        updated = repo.update(outage_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    OutageEventRepository(db).record(outage_id, "updated", payload.model_dump(exclude_unset=True, exclude_none=True))
    return updated


@router.patch("/{outage_id}", response_model=Outage)
def patch_outage(outage_id: str, payload: OutageUpdate, db: Session = Depends(get_db)):
    repo = OutageRepository(db)
    existing = repo.get(outage_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Outage not found")

    try:
        updated = repo.update(outage_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    OutageEventRepository(db).record(outage_id, "patched", payload.model_dump(exclude_unset=True, exclude_none=True))
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
    try:
        outage = repo.resolve(outage_id, payload.mttr_minutes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not outage:
        raise HTTPException(status_code=404, detail="Outage not found")

    audit_log.log("outage_resolved", {"id": outage.id, "mttr": payload.mttr_minutes})
    OutageEventRepository(db).record(outage_id, "resolved", {"mttr_minutes": payload.mttr_minutes})

    raw_contract_result = SLAContractAdapter.calculate_sla(
        outage_id=outage.id,
        severity=outage.severity,
        mttr_minutes=payload.mttr_minutes,
    )
    sla = translate_contract_result(raw_contract_result)

    sla_repo = SLARepository(db)
    stored_sla = sla_repo.create_if_changed(sla)
    OutageEventRepository(db).record(outage_id, "sla_computed", {"status": stored_sla.status})
    payment_repo = PaymentRepository(db)
    payment = payment_repo.create_for_sla_result(outage.id, stored_sla)
    webhook_event = WebhookEvent.SLA_VIOLATION if stored_sla.status == "violated" else WebhookEvent.SLA_RESOLVED
    trigger_sla_violation_webhooks(
        db,
        sla_data={"outage_id": outage.id, "sla": stored_sla.model_dump(), "payment": payment.model_dump()},
        event=webhook_event,
    )

    return {"outage": outage, "sla": stored_sla, "payment": payment}


@router.post("/{outage_id}/recompute-sla")
def recompute_sla(outage_id: str, db: Session = Depends(get_db)):
    repo = OutageRepository(db)
    outage = repo.get(outage_id)
    if not outage:
        raise HTTPException(status_code=404, detail="Outage not found")

    if outage.status != OutageStatus.resolved:
        raise HTTPException(status_code=400, detail="Outage not resolved yet")

    orm = repo.get_orm(outage_id)
    raw_contract_result = SLAContractAdapter.calculate_sla(
        outage_id=outage.id,
        severity=outage.severity,
        mttr_minutes=orm.mttr_minutes,
    )
    sla = translate_contract_result(raw_contract_result)

    sla_repo = SLARepository(db)
    stored_sla = sla_repo.create_if_changed(sla)
    payment_repo = PaymentRepository(db)
    payment = payment_repo.create_for_sla_result(outage.id, stored_sla)
    webhook_event = WebhookEvent.SLA_VIOLATION if stored_sla.status == "violated" else WebhookEvent.SLA_RESOLVED
    trigger_sla_violation_webhooks(
        db,
        sla_data={"outage_id": outage.id, "sla": stored_sla.model_dump(), "payment": payment.model_dump()},
        event=webhook_event,
    )

    audit_log.log("sla_recomputed", {"id": outage.id})
    OutageEventRepository(db).record(outage_id, "sla_recomputed", {"status": stored_sla.status})
    return {"sla": stored_sla, "payment": payment}


@router.get("/{outage_id}/timeline")
def get_outage_timeline(outage_id: str, db: Session = Depends(get_db)):
    repo = OutageRepository(db)
    if not repo.get(outage_id):
        raise HTTPException(status_code=404, detail="Outage not found")
    return OutageEventRepository(db).list_for_outage(outage_id)
