from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session
from app.api import deps
from app.schemas.idempotency import IdempotencyKeyCreate, IdempotencyKeyResponse
from app.services.idempotency import IdempotencyService

router = APIRouter()
service = IdempotencyService()

@router.post("/financial-transaction", response_model=IdempotencyKeyResponse)
def process_financial_transaction(
    *,
    db: Session = Depends(deps.get_db),
    idempotency_key: str = Header(...),
):
    """
    Process a financial transaction idempotently.
    """
    key_in = IdempotencyKeyCreate(key=idempotency_key, endpoint="/financial-transaction")
    return service.process_key(db, key_in=key_in)
