import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.orm.outage import OutageORM
from app.models.orm.outage_event import OutageEventORM
from app.models.orm.sla import SLAResultORM
from app.models.orm.payment import PaymentTransactionORM
from app.models.webhook import WebhookDelivery
from app.schemas.trace import TraceChain, TraceNode

logger = logging.getLogger(__name__)


def _serialize_dt(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return dt.isoformat()


def _resolve_outage_id(
    db: Session,
    outage_id: Optional[str] = None,
    payment_id: Optional[str] = None,
    tx_hash: Optional[str] = None,
) -> Optional[str]:
    """Resolve the target outage_id from any of the supported filter criteria.

    Priority: outage_id > payment_id > tx_hash
    Returns None when no resolution is possible.
    """
    if outage_id:
        return outage_id

    if payment_id:
        payment_orm = (
            db.query(PaymentTransactionORM)
            .filter(PaymentTransactionORM.id == payment_id)
            .first()
        )
        if payment_orm:
            return payment_orm.outage_id
        logger.warning("Payment %s not found when resolving trace outage_id", payment_id)
        return None

    if tx_hash:
        payment_orm = (
            db.query(PaymentTransactionORM)
            .filter(PaymentTransactionORM.transaction_hash == tx_hash)
            .first()
        )
        if payment_orm:
            return payment_orm.outage_id
        logger.warning("Transaction hash %s not found when resolving trace outage_id", tx_hash)
        return None

    return None


def build_trace_chain(
    db: Session,
    outage_id: Optional[str] = None,
    payment_id: Optional[str] = None,
    tx_hash: Optional[str] = None,
) -> TraceChain:
    """Build an ordered causal trace chain linking outage → SLA → payment → webhook.

    Accepts one of:
      - outage_id: direct outage lookup
      - payment_id: resolves to outage via payment.outage_id
      - tx_hash:    resolves to outage via payment.transaction_hash

    Returns a :class:`TraceChain` with sequentially ordered :class:`TraceNode` entries.
    """
    resolved_outage_id = _resolve_outage_id(db, outage_id, payment_id, tx_hash)
    if not resolved_outage_id:
        return TraceChain(
            outage_id=outage_id or None,
            payment_id=payment_id or None,
            transaction_hash=tx_hash or None,
            nodes=[],
            total_nodes=0,
        )

    nodes: List[TraceNode] = []
    sequence = 0

    # --- 1. Outage record ---
    outage_orm = db.query(OutageORM).filter(OutageORM.id == resolved_outage_id).first()
    if not outage_orm:
        return TraceChain(
            outage_id=resolved_outage_id,
            nodes=[],
            total_nodes=0,
        )

    sequence += 1
    nodes.append(
        TraceNode(
            sequence=sequence,
            entity_type="outage",
            entity_id=outage_orm.id,
            timestamp=_serialize_dt(outage_orm.detected_at),
            summary=f"Outage {outage_orm.id}: severity={outage_orm.severity}, status={outage_orm.status}",
            details={
                "site_name": outage_orm.site_name,
                "site_id": outage_orm.site_id,
                "severity": outage_orm.severity,
                "status": outage_orm.status,
                "detected_at": _serialize_dt(outage_orm.detected_at),
                "resolved_at": _serialize_dt(outage_orm.resolved_at),
                "description": outage_orm.description,
                "affected_services": outage_orm.affected_services or [],
                "affected_subscribers": outage_orm.affected_subscribers,
                "assigned_to": outage_orm.assigned_to,
                "created_by": outage_orm.created_by,
                "mttr_minutes": outage_orm.mttr_minutes,
            },
        )
    )

    # --- 2. Outage events (timeline) ---
    events = (
        db.query(OutageEventORM)
        .filter(OutageEventORM.outage_id == resolved_outage_id)
        .order_by(OutageEventORM.occurred_at.asc())
        .all()
    )
    for evt in events:
        sequence += 1
        detail_parsed = json.loads(evt.detail) if evt.detail else {}
        nodes.append(
            TraceNode(
                sequence=sequence,
                entity_type="outage_event",
                entity_id=evt.id,
                timestamp=_serialize_dt(evt.occurred_at),
                summary=f"Event: {evt.event_type}",
                details={
                    "event_type": evt.event_type,
                    "schema_version": evt.schema_version,
                    "detail": detail_parsed,
                },
            )
        )

    # --- 3. SLA results ---
    sla_results = (
        db.query(SLAResultORM)
        .filter(SLAResultORM.outage_id == resolved_outage_id)
        .order_by(SLAResultORM.created_at.asc())
        .all()
    )
    for sla in sla_results:
        sequence += 1
        nodes.append(
            TraceNode(
                sequence=sequence,
                entity_type="sla_result",
                entity_id=str(sla.id),
                timestamp=_serialize_dt(sla.created_at),
                summary=f"SLA {sla.id}: status={sla.status}, rating={sla.rating}, amount={sla.amount} ({sla.payment_type})",
                details={
                    "status": sla.status,
                    "mttr_minutes": sla.mttr_minutes,
                    "threshold_minutes": sla.threshold_minutes,
                    "amount": sla.amount,
                    "payment_type": sla.payment_type,
                    "rating": sla.rating,
                    "policy_version": sla.policy_version,
                    "threshold_source": sla.threshold_source,
                    "reason_code": sla.reason_code,
                    "decision_trace": sla.decision_trace,
                    "is_latest": sla.is_latest,
                },
            )
        )

    # --- 4. Payment transactions ---
    payments = (
        db.query(PaymentTransactionORM)
        .filter(PaymentTransactionORM.outage_id == resolved_outage_id)
        .order_by(PaymentTransactionORM.created_at.asc())
        .all()
    )
    for pmt in payments:
        sequence += 1
        nodes.append(
            TraceNode(
                sequence=sequence,
                entity_type="payment",
                entity_id=pmt.id,
                timestamp=_serialize_dt(pmt.created_at),
                summary=f"Payment {pmt.id}: type={pmt.type}, amount={pmt.amount} {pmt.asset_code}, status={pmt.status}",
                details={
                    "transaction_hash": pmt.transaction_hash,
                    "type": pmt.type,
                    "amount": pmt.amount,
                    "asset_code": pmt.asset_code,
                    "from_address": pmt.from_address,
                    "to_address": pmt.to_address,
                    "status": pmt.status,
                    "created_at": _serialize_dt(pmt.created_at),
                    "confirmed_at": _serialize_dt(pmt.confirmed_at),
                    "retry_count": pmt.retry_count,
                    "last_retried_at": _serialize_dt(pmt.last_retried_at),
                    "failure_taxonomy": pmt.failure_taxonomy,
                    "idempotency_key": pmt.idempotency_key,
                    "dead_letter_reason": pmt.dead_letter_reason,
                    "dead_lettered_at": _serialize_dt(pmt.dead_lettered_at),
                    "sla_result_id": pmt.sla_result_id,
                },
            )
        )

    # --- 5. Webhook deliveries ---
    # Webhook deliveries store outage context in the JSON payload's data.outage_id field.
    # Only scan SLA-related event types (sla.violation, sla.warning, sla.resolved) since
    # those are the only events that carry outage_id in their payload.
    from app.models.webhook import WebhookEvent

    sla_events = [
        WebhookEvent.SLA_VIOLATION,
        WebhookEvent.SLA_WARNING,
        WebhookEvent.SLA_RESOLVED,
    ]
    webhook_deliveries = (
        db.query(WebhookDelivery)
        .filter(WebhookDelivery.event.in_(sla_events))
        .order_by(WebhookDelivery.created_at.asc())
        .all()
    )
    for wd in webhook_deliveries:
        # Parse payload to find deliveries related to this outage
        try:
            payload = json.loads(wd.payload) if wd.payload else {}
            data = payload.get("data", {})
            payload_outage_id = data.get("outage_id")
            if payload_outage_id != resolved_outage_id:
                continue
        except (json.JSONDecodeError, TypeError, AttributeError):
            continue

        sequence += 1
        nodes.append(
            TraceNode(
                sequence=sequence,
                entity_type="webhook_delivery",
                entity_id=str(wd.id),
                timestamp=_serialize_dt(wd.created_at),
                summary=f"Webhook delivery {wd.id}: event={wd.event.value}, status={wd.status.value}, attempt={wd.attempt_count}",
                details={
                    "webhook_id": str(wd.webhook_id) if wd.webhook_id else None,
                    "event": wd.event.value,
                    "status": wd.status.value,
                    "attempt_count": wd.attempt_count,
                    "response_status_code": wd.response_status_code,
                    "error_message": wd.error_message,
                    "delivered_at": _serialize_dt(wd.delivered_at),
                    "dead_lettered_at": _serialize_dt(wd.dead_lettered_at),
                    "signature_version": wd.signature_version,
                    "idempotency_key": wd.idempotency_key,
                    "event_timestamp": _serialize_dt(wd.event_timestamp),
                },
            )
        )

    return TraceChain(
        outage_id=resolved_outage_id,
        payment_id=payment_id or None,
        transaction_hash=tx_hash or None,
        nodes=nodes,
        total_nodes=len(nodes),
    )
