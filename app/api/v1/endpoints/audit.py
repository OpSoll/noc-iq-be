from fastapi import APIRouter, Depends, Query
from typing import Optional
from app.services.audit_log import audit_log, BridgeOutcomeClass
from app.core.security import require_admin

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("")
def get_audit_log(
    event_type_prefix: Optional[str] = Query(
        None,
        description=(
            "Filter events by type prefix. "
            "Use 'wallet.' to return all wallet-related events. "
            "Use 'bridge.' to return all contract bridge events."
        ),
    ),
    bridge_outcome: Optional[str] = Query(
        None,
        description=(
            "Filter bridge.* events by outcome class. "
            f"One of: {BridgeOutcomeClass.SUCCESS!r}, {BridgeOutcomeClass.TRANSIENT_ERROR!r}, "
            f"{BridgeOutcomeClass.SEMANTIC_ERROR!r}, {BridgeOutcomeClass.DEGRADED!r}, {BridgeOutcomeClass.UNKNOWN!r}."
        ),
    ),
    limit: int = Query(50, ge=1, le=500, description="Maximum number of records to return."),
    offset: int = Query(0, ge=0, description="Number of records to skip for pagination."),
    current_user=Depends(require_admin),
):
    return audit_log.list(
        event_type_prefix=event_type_prefix,
        bridge_outcome=bridge_outcome,
        limit=limit,
        offset=offset,
    )
