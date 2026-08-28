from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.tx_provenance import (
    TxProvenanceResponse,
    TxVerifyRequest,
    TxVerifyResponse,
)
from app.services.tx_provenance import TxProvenanceService
from app.core.security import get_current_user

router = APIRouter()


@router.get("/{tx_hash}/provenance", response_model=TxProvenanceResponse)
def get_tx_provenance(
    tx_hash: str,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    svc = TxProvenanceService(db)
    provenance = svc.get_status(tx_hash)
    if not provenance:
        raise HTTPException(status_code=404, detail="Transaction provenance not found")
    return TxProvenanceResponse(
        tx_hash=provenance.tx_hash,
        network=provenance.network,
        status=provenance.status,
        submitted_at=provenance.submitted_at,
        confirmed_at=provenance.confirmed_at,
        verified_at=provenance.verified_at,
        block_number=provenance.block_number,
    )


@router.post("/{tx_hash}/verify", response_model=TxVerifyResponse)
def verify_tx(
    tx_hash: str,
    payload: TxVerifyRequest,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    svc = TxProvenanceService(db)
    provenance = svc.get_status(tx_hash)
    if not provenance:
        raise HTTPException(status_code=404, detail="Transaction provenance not found")

    if provenance.status == "verified" and not payload.force:
        return TxVerifyResponse(
            tx_hash=tx_hash,
            status="verified",
            verified_at=provenance.verified_at,
            message="Transaction already verified",
        )

    updated = svc.verify(tx_hash)
    return TxVerifyResponse(
        tx_hash=tx_hash,
        status=updated.status,
        verified_at=updated.verified_at,
        message="Transaction verification completed",
    )
