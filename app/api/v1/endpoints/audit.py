from fastapi import APIRouter, Depends, Query
from typing import Optional
from app.services.audit_log import audit_log
from app.core.security import require_admin

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("")
def get_audit_log(
    event_type_prefix: Optional[str] = Query(
        None,
        description=(
            "Filter events by type prefix. "
            "Use 'wallet.' to return all wallet-related events."
        ),
    ),
    limit: int = Query(50, ge=1, le=500, description="Maximum number of records to return."),
    offset: int = Query(0, ge=0, description="Number of records to skip for pagination."),
    current_user=Depends(require_admin),
):
    return audit_log.list(
        event_type_prefix=event_type_prefix,
        limit=limit,
        offset=offset,
    )
