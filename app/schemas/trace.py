from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class TraceNode(BaseModel):
    """A single node in the causal trace chain."""

    sequence: int
    entity_type: str  # "outage", "outage_event", "sla_result", "payment", "webhook_delivery"
    entity_id: str
    timestamp: Optional[str] = None
    summary: str
    details: Dict[str, Any] = {}


class TraceChain(BaseModel):
    """Ordered causal chain linking outage → SLA → payment → webhook."""

    outage_id: Optional[str] = None
    payment_id: Optional[str] = None
    transaction_hash: Optional[str] = None
    nodes: List[TraceNode] = []
    total_nodes: int = 0
