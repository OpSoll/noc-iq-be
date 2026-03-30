from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.payment import PaginatedPayments, PaymentTransaction
from app.repositories.payment_repository import PaymentRepository
from app.services.audit_log import audit_log

router = APIRouter()


@router.get("/", response_model=PaginatedPayments)
def list_payments(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status: Optional[str] = None,
    type: Optional[str] = None,
    outage_id: Optional[str] = None,
    date_from: Optional[datetime] = Query(default=None),
    date_to: Optional[datetime] = Query(default=None),
    db: Session = Depends(get_db),
):
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=400, detail="date_from cannot be after date_to")
    repo = PaymentRepository(db)
    items, total = repo.list(
        page=page,
        page_size=page_size,
        status=status,
        outage_id=outage_id,
        type=type,
        date_from=date_from,
        date_to=date_to,
    )
    return PaginatedPayments(items=items, total=total, page=page, page_size=page_size)


@router.get("/ping")
def payments_ping():
    return {"message": "payments ok"}


@router.get("/{transaction_id}", response_model=PaymentTransaction)
def get_payment(transaction_id: str, db: Session = Depends(get_db)):
    repo = PaymentRepository(db)
    payment = repo.get(transaction_id)
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    return payment


class ReconcileRequest(BaseModel):
    status: str


@router.post("/{transaction_id}/reconcile", response_model=PaymentTransaction)
def reconcile_payment(transaction_id: str, payload: ReconcileRequest, db: Session = Depends(get_db)):
    repo = PaymentRepository(db)
    payment = repo.reconcile(transaction_id, payload.status)
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    audit_log.log("payment_reconciled", {"id": transaction_id, "status": payload.status})
    return payment


@router.post("/{transaction_id}/retry", response_model=PaymentTransaction)
def retry_payment(transaction_id: str, db: Session = Depends(get_db)):
    repo = PaymentRepository(db)
    existing = repo.get(transaction_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Payment not found")
    payment = repo.retry(transaction_id)
    if not payment:
        raise HTTPException(status_code=409, detail="Max retries reached")
    audit_log.log("payment_retried", {"id": transaction_id, "retry_count": payment.retry_count})
    return payment
