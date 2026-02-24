from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.sla_dispute import SLADispute, DisputeStatus
from app.schemas.sla_dispute import DisputeFlagRequest, DisputeResolveRequest, DisputeResponse

router = APIRouter()


@router.post(
    "/{sla_result_id}/dispute",
    response_model=DisputeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Flag an SLA result for dispute",
)
def flag_dispute(
    sla_result_id: UUID,
    payload: DisputeFlagRequest,
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
    db.commit()
    db.refresh(dispute)
    return dispute


@router.put(
    "/{sla_result_id}/dispute/resolve",
    response_model=DisputeResponse,
    summary="Resolve or reject an SLA dispute",
)
def resolve_dispute(
    sla_result_id: UUID,
    payload: DisputeResolveRequest,
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

    db.commit()
    db.refresh(dispute)
    return dispute


@router.get(
    "/{sla_result_id}/dispute",
    response_model=DisputeResponse,
    summary="Get the dispute for an SLA result",
)
def get_dispute(
    sla_result_id: UUID,
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
