from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.api import deps
from app.schemas.dispute import DisputeCreate, DisputeUpdate, DisputeResponse
from app.services.dispute import DisputeService

router = APIRouter()
service = DisputeService()

@router.post("/", response_model=DisputeResponse)
def open_sla_dispute(
    *,
    db: Session = Depends(deps.get_db),
    dispute_in: DisputeCreate,
):
    """
    Open a new SLA dispute.
    """
    return service.open_dispute(db, dispute_in=dispute_in)

@router.patch("/{dispute_id}", response_model=DisputeResponse)
def update_sla_dispute(
    *,
    db: Session = Depends(deps.get_db),
    dispute_id: int,
    update_in: DisputeUpdate,
):
    """
    Update SLA dispute state and resolution notes.
    """
    dispute = service.update_dispute_state(db, dispute_id=dispute_id, update_in=update_in)
    if not dispute:
        raise HTTPException(status_code=404, detail="Dispute not found")
    return dispute
