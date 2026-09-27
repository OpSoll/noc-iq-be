from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.api import deps
from app.schemas.logging import RequestLogCreate, RequestLogResponse
from app.services.logging import LoggingService

router = APIRouter()
service = LoggingService()

@router.post("/log", response_model=RequestLogResponse)
def create_request_log(
    *,
    db: Session = Depends(deps.get_db),
    log_in: RequestLogCreate,
):
    """
    Store a request log with automatic PII redaction.
    """
    return service.log_request(db, log_in=log_in)
