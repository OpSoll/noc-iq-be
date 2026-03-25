from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.payment import PaginatedPayments, PaymentTransaction
from app.repositories.payment_repository import PaymentRepository

router = APIRouter()


@router.get("/", response_model=PaginatedPayments)
def list_payments(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status: str | None = None,
    outage_id: str | None = None,
    db: Session = Depends(get_db),
):
    repo = PaymentRepository(db)
    items, total = repo.list(
        page=page,
        page_size=page_size,
        status=status,
        outage_id=outage_id,
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
