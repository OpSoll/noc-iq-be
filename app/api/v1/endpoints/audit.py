from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.audit_log import audit_log, BridgeOutcomeClass
from app.services.trace import build_trace_chain
from app.schemas.trace import TraceChain
from app.core.security import require_admin

router = APIRouter(prefix="/audit", tags=["audit"])


class AuditLogEntry(BaseModel):
    """A single structured audit log record."""
    id: int = Field(..., description="Audit record ID")
    event_type: str = Field(..., description="Event type (dot-namespaced, e.g. wallet.created)")
    email: Optional[str] = Field(None, description="Actor email, when available")
    actor_id: Optional[str] = Field(None, description="Actor identifier, when available")
    correlation_id: Optional[str] = Field(None, description="Correlation ID tying the event to a request")
    details: Optional[Dict[str, Any]] = Field(None, description="Structured event details")
    created_at: Optional[str] = Field(None, description="ISO timestamp the event was recorded")


@router.get("", response_model=List[AuditLogEntry])
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


@router.get("/trace", response_model=TraceChain)
def get_trace(
    outage_id: Optional[str] = Query(
        None,
        description="Filter trace by outage ID. Resolves to the outage record and its causal chain.",
    ),
    payment_id: Optional[str] = Query(
        None,
        description="Filter trace by payment transaction ID. Resolves to the parent outage.",
    ),
    tx_hash: Optional[str] = Query(
        None,
        description="Filter trace by transaction hash (blockchain). Resolves to the parent payment and outage.",
    ),
    current_user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Return an ordered causal trace chain linking outage → SLA → payment → webhook.

    **BE-W5-060: Outage-to-payment trace view for incident forensics.**

    At least one filter parameter (`outage_id`, `payment_id`, or `tx_hash`) must be
    provided. The endpoint resolves the chain by walking foreign-key relationships:

    1. Outage record and its event timeline
    2. SLA results computed for the outage
    3. Payment transactions created from SLA results
    4. Webhook deliveries triggered by SLA events

    **Access control:**
    - Restricted to `admin` role only
    - Every access is written to the audit log with actor and filter context

    **Stable identifiers:**
    - Each node carries a `sequence` (stable ordering)
    - `entity_type` + `entity_id` uniquely identify the source record
    """
    if not any([outage_id, payment_id, tx_hash]):
        raise HTTPException(
            status_code=400,
            detail="At least one filter parameter is required: outage_id, payment_id, or tx_hash",
        )

    chain = build_trace_chain(
        db=db,
        outage_id=outage_id,
        payment_id=payment_id,
        tx_hash=tx_hash,
    )

    # Return 404 when the resolved outage does not exist
    if chain.total_nodes == 0 and not chain.outage_id:
        raise HTTPException(
            status_code=404,
            detail="No trace chain found for the given filter criteria. The outage, payment, or transaction hash may not exist.",
        )

    # Audit-log every trace access with actor and filter context
    actor_email = getattr(current_user, "email", "unknown")
    audit_log.log(
        "trace.accessed",
        details={
            "outage_id": outage_id,
            "payment_id": payment_id,
            "tx_hash": tx_hash,
            "total_nodes": chain.total_nodes,
            "resolved_outage_id": chain.outage_id,
            "actor": actor_email,
        },
    )

    return chain
