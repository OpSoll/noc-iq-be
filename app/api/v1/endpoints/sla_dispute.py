from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.sla_dispute import DisputeAuditLog, SLADispute, DisputeStatus
from app.schemas.sla_dispute import DisputeAuditLogResponse, DisputeFlagRequest, DisputeResolveRequest, DisputeResponse
from app.core.security import require_engineer, require_admin

router = APIRouter()


@router.get(
    "/disputes",
    response_model=list[DisputeResponse],
    summary="List SLA disputes",
)
def list_disputes(
    status_filter: DisputeStatus | None = Query(default=None, alias="status"),
    current_user=Depends(require_engineer),
    db: Session = Depends(get_db),
):
    query = db.query(SLADispute).order_by(SLADispute.flagged_at.desc())
    if status_filter is not None:
        query = query.filter(SLADispute.status == status_filter)
    return query.all()


@router.post(
    "/{sla_result_id}/dispute",
    response_model=DisputeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Flag an SLA result for dispute",
)
def flag_dispute(
    sla_result_id: int,
    payload: DisputeFlagRequest,
    current_user=Depends(require_engineer),
    db: Session = Depends(get_db),
):
    existing = (
        db.query(SLADispute)
        .filter(
            SLADispute.sla_result_id == sla_result_id,
            SLADispute.status == DisputeStatus.PENDING,
        )
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An active dispute already exists for this SLA result.",
        )

    dispute = SLADispute(
        sla_result_id=sla_result_id,
        flagged_by=payload.flagged_by,
        dispute_reason=payload.dispute_reason,
    )
    db.add(dispute)
    db.flush()

    db.add(DisputeAuditLog(
        dispute_id=dispute.id,
        action="flagged",
        actor=payload.flagged_by,
        notes=payload.dispute_reason,
    ))
    db.commit()
    db.refresh(dispute)
    return dispute


@router.put(
    "/{sla_result_id}/dispute/resolve",
    response_model=DisputeResponse,
    summary="Resolve or reject an SLA dispute",
)
def resolve_dispute(
    sla_result_id: int,
    payload: DisputeResolveRequest,
    current_user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    if payload.status not in (DisputeStatus.RESOLVED, DisputeStatus.REJECTED):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Resolution status must be 'resolved' or 'rejected'.",
        )

    dispute = (
        db.query(SLADispute)
        .filter(
            SLADispute.sla_result_id == sla_result_id,
            SLADispute.status == DisputeStatus.PENDING,
        )
        .first()
    )
    if not dispute:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No pending dispute found for this SLA result.",
        )

    dispute.status = payload.status
    dispute.resolved_by = payload.resolved_by
    dispute.resolution_notes = payload.resolution_notes
    dispute.resolved_at = datetime.utcnow()

    db.add(DisputeAuditLog(
        dispute_id=dispute.id,
        action=payload.status.value,
        actor=payload.resolved_by,
        notes=payload.resolution_notes,
    ))
    db.commit()
    db.refresh(dispute)
    return dispute


@router.get(
    "/{sla_result_id}/dispute",
    response_model=DisputeResponse,
    summary="Get the dispute for an SLA result",
)
def get_dispute(
    sla_result_id: int,
    db: Session = Depends(get_db),
):
    dispute = (
        db.query(SLADispute)
        .filter(SLADispute.sla_result_id == sla_result_id)
        .order_by(SLADispute.flagged_at.desc())
        .first()
    )
    if not dispute:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No dispute found for this SLA result.",
        )
    return dispute


@router.get(
    "/{sla_result_id}/dispute/history",
    response_model=list[DisputeAuditLogResponse],
    summary="Get audit trail for a dispute",
)
def get_dispute_history(
    sla_result_id: int,
    db: Session = Depends(get_db),
):
    dispute = (
        db.query(SLADispute)
        .filter(SLADispute.sla_result_id == sla_result_id)
        .order_by(SLADispute.flagged_at.desc())
        .first()
    )
    if not dispute:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No dispute found for this SLA result.",
        )
    return dispute.audit_logs
