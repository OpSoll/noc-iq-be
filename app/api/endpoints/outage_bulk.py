from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.api import deps
from app.schemas.outage_bulk import BulkOutageCreate, BulkOutageResponse
from app.services.outage_bulk import OutageBulkService

router = APIRouter()
service = OutageBulkService()

@router.post("/bulk", response_model=BulkOutageResponse)
def create_bulk_outages(
    *,
    db: Session = Depends(deps.get_db),
    payload: BulkOutageCreate,
):
    """
    Create multiple outage records in a single database transaction.
    Rolls back entirely if any record fails.
    """
    return service.create_bulk_outages(db, payload=payload)
