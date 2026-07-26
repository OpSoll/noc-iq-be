from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.payment import PaginatedPayments, PaymentTransaction
from app.repositories.payment_repository import PaymentRepository
from app.services.idempotency_service import IdempotencyService

router = APIRouter()


@router.get("/idempotency/metrics")
def idempotency_metrics(db: Session = Depends(get_db)):
    service = IdempotencyService(db)
    return service.get_metrics()


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


@router.post("/{outage_id}/create", response_model=PaymentTransaction)
def create_payment_for_outage(
    outage_id: str,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    idempotency_key = request.headers.get("X-Idempotency-Key")
    service = IdempotencyService(db)

    if idempotency_key:
        cached = service.lookup(idempotency_key)
        if cached is not None:
            response.status_code = cached["status_code"]
            return cached["response_json"]

    repo = PaymentRepository(db)
    from app.models.sla import SLAResult
    from datetime import datetime

    payment = PaymentTransaction(
        id=f"pay_{outage_id[:12]}",
        transaction_hash=f"outage-{outage_id}",
        type="sla_settlement",
        amount=0.0,
        asset_code="USDC",
        from_address="SYSTEM_POOL",
        to_address="OUTAGE_SETTLEMENT",
        status="pending",
        outage_id=outage_id,
        sla_result_id=None,
        created_at=datetime.utcnow(),
        confirmed_at=None,
    )
    created = repo.create(payment)

    if idempotency_key:
        service.store(idempotency_key, created.model_dump(mode="json"), 201)

    response.status_code = 201
    return created
