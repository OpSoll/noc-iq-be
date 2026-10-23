"""Tests for correlation trace chain assembly (issue #987).

Covers `app.services.trace.build_trace_chain` (BE-W5-060) and the
`GET /api/v1/audit/trace` endpoint:

- Ordered chains: outage → outage events → SLA results → payments → webhook
  deliveries, with sequentially numbered, causally ordered nodes.
- Broken chains: unresolvable payment/tx filters, missing outage records, and
  webhook deliveries that do not belong to the traced outage.
- Empty chains: no filter criteria or empty-string filters.
"""

import json
import uuid
from datetime import datetime, timedelta

import pytest

from app.models.orm.outage import OutageORM
from app.models.orm.outage_event import OutageEventORM
from app.models.orm.payment import PaymentTransactionORM
from app.models.orm.sla import SLAResultORM
from app.models.webhook import Webhook, WebhookDelivery, WebhookDeliveryStatus, WebhookEvent
from app.services.trace import build_trace_chain

BASE_TIME = datetime(2026, 2, 3, 10, 0, 0)


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _make_outage(db, outage_id: str) -> OutageORM:
    outage = OutageORM(
        id=outage_id,
        site_name="Trace Site",
        site_id="site-trace",
        severity="high",
        status="open",
        detected_at=BASE_TIME,
        description="Trace chain test outage",
        affected_services=["core-api"],
        affected_subscribers=7,
        assigned_to="oncall@example.com",
        created_by="tester@example.com",
    )
    db.add(outage)
    return outage


def _make_event(db, outage_id: str, event_type: str, occurred_at: datetime, detail=None) -> OutageEventORM:
    evt = OutageEventORM(
        id=_unique("evt"),
        outage_id=outage_id,
        event_type=event_type,
        detail=json.dumps(detail) if detail is not None else None,
        schema_version="1",
        occurred_at=occurred_at,
    )
    db.add(evt)
    return evt


def _make_sla_result(db, outage_id: str, created_at: datetime) -> SLAResultORM:
    sla = SLAResultORM(
        outage_id=outage_id,
        status="violated",
        mttr_minutes=90,
        threshold_minutes=60,
        amount=250.0,
        payment_type="penalty",
        rating="poor",
        policy_version="1.0",
        threshold_source="config",
        is_latest=True,
        created_at=created_at,
        reason_code="mttr_exceeded",
        decision_trace="mttr 90 > threshold 60",
    )
    db.add(sla)
    db.flush()  # populate sla.id for the payment FK
    return sla


def _make_payment(db, outage_id: str, sla_result_id, created_at: datetime, tx_hash: str = None) -> PaymentTransactionORM:
    payment = PaymentTransactionORM(
        id=_unique("payment"),
        transaction_hash=tx_hash or _unique("tx"),
        type="penalty",
        amount=250.0,
        asset_code="USDC",
        from_address="SYSTEM_POOL",
        to_address="OUTAGE_SETTLEMENT",
        status="confirmed",
        outage_id=outage_id,
        sla_result_id=sla_result_id,
        created_at=created_at,
        confirmed_at=created_at + timedelta(minutes=1),
        retry_count=0,
    )
    db.add(payment)
    db.flush()  # populate payment.id for webhook/delivery references
    return payment


def _make_webhook_delivery(db, payload: str, created_at: datetime) -> WebhookDelivery:
    webhook = Webhook(
        name=_unique("trace-hook"),
        url="https://example.com/webhook",
        secret="secret",
        events='["sla.violation"]',
    )
    db.add(webhook)
    db.flush()
    # Provide a deterministic idempotency_key to satisfy NOT NULL constraint
    delivery = WebhookDelivery(
        webhook_id=webhook.id,
        event=WebhookEvent.SLA_VIOLATION,
        payload=payload,
        status=WebhookDeliveryStatus.SUCCESS,
        attempt_count=1,
        response_status_code=200,
        delivered_at=created_at,
        created_at=created_at,
        event_timestamp=created_at,
        idempotency_key=_unique("idem"),  # Required: NOT NULL unique
    )
    db.add(delivery)
    return delivery


# ---------------------------------------------------------------------------
# Ordered chain
# ---------------------------------------------------------------------------


def test_ordered_chain_covers_full_outage_to_webhook_flow(db):
    """A complete chain yields sequentially numbered nodes in causal order."""
    outage_id = _unique("outage")
    outage = _make_outage(db, outage_id)
    created_evt = _make_event(db, outage_id, "created", BASE_TIME + timedelta(minutes=1), detail={"source": "test"})
    resolved_evt = _make_event(db, outage_id, "resolved", BASE_TIME + timedelta(minutes=30))
    sla = _make_sla_result(db, outage_id, BASE_TIME + timedelta(minutes=35))
    tx_hash = _unique("tx")
    payment = _make_payment(db, outage_id, sla.id, BASE_TIME + timedelta(minutes=40), tx_hash=tx_hash)
    delivery = _make_webhook_delivery(
        db,
        json.dumps({"data": {"outage_id": outage_id}}),
        BASE_TIME + timedelta(minutes=42),
    )
    db.commit()

    chain = build_trace_chain(db, outage_id=outage_id)

    try:
        assert chain.outage_id == outage_id
        assert chain.total_nodes == len(chain.nodes) == 6

        # Sequences are contiguous and strictly ascending from 1
        assert [n.sequence for n in chain.nodes] == [1, 2, 3, 4, 5, 6]

        # Causal ordering: outage → events → SLA → payment → webhook
        assert [n.entity_type for n in chain.nodes] == [
            "outage",
            "outage_event",
            "outage_event",
            "sla_result",
            "payment",
            "webhook_delivery",
        ]

        # (entity_type, entity_id) uniquely identify each source record
        assert len({(n.entity_type, n.entity_id) for n in chain.nodes}) == chain.total_nodes

        outage_node, event_a, event_b, sla_node, payment_node, webhook_node = chain.nodes

        assert outage_node.entity_id == outage.id
        assert outage_node.timestamp == BASE_TIME.isoformat()
        assert outage_node.details["severity"] == "high"
        assert outage_node.details["status"] == "open"
        assert outage_node.details["affected_services"] == ["core-api"]

        # Events ordered by occurred_at, not insertion order
        assert event_a.entity_id == created_evt.id
        assert event_a.details["detail"] == {"source": "test"}
        assert event_b.entity_id == resolved_evt.id
        assert event_b.details["detail"] == {}
        assert event_a.timestamp < event_b.timestamp

        assert sla_node.entity_id == str(sla.id)
        assert sla_node.details["amount"] == 250.0
        assert sla_node.details["rating"] == "poor"
        assert sla_node.details["is_latest"] is True
        assert sla_node.details["reason_code"] == "mttr_exceeded"

        assert payment_node.entity_id == payment.id
        assert payment_node.details["transaction_hash"] == tx_hash
        assert payment_node.details["sla_result_id"] == sla.id
        assert payment_node.details["status"] == "confirmed"

        assert webhook_node.entity_id == str(delivery.id)
        assert webhook_node.details["event"] == "sla.violation"
        assert webhook_node.details["status"] == "success"
        assert webhook_node.details["attempt_count"] == 1
        assert webhook_node.details["webhook_id"] == str(delivery.webhook_id)
    finally:
        for model in (WebhookDelivery, Webhook, PaymentTransactionORM, SLAResultORM, OutageEventORM, OutageORM):
            db.query(model).delete()
        db.commit()


def test_chain_orders_outage_events_by_occurred_not_insertion_order(db):
    """Outage events are sorted by occurred_at even when inserted out of order."""
    outage_id = _unique("outage")
    _make_outage(db, outage_id)
    late = _make_event(db, outage_id, "resolved", BASE_TIME + timedelta(hours=2))
    early = _make_event(db, outage_id, "created", BASE_TIME)
    middle = _make_event(db, outage_id, "sla_computed", BASE_TIME + timedelta(hours=1))
    db.commit()

    try:
        chain = build_trace_chain(db, outage_id=outage_id)
        event_nodes = [n for n in chain.nodes if n.entity_type == "outage_event"]
        assert [n.entity_id for n in event_nodes] == [early.id, middle.id, late.id]
        assert [n.sequence for n in event_nodes] == [2, 3, 4]
    finally:
        for model in (OutageEventORM, OutageORM):
            db.query(model).delete()
        db.commit()


def test_chain_resolves_outage_from_payment_id(db):
    """payment_id filter resolves to the parent outage and its chain."""
    outage_id = _unique("outage")
    _make_outage(db, outage_id)
    sla = _make_sla_result(db, outage_id, BASE_TIME)
    payment = _make_payment(db, outage_id, sla.id, BASE_TIME)
    db.commit()

    try:
        chain = build_trace_chain(db, payment_id=payment.id)
        assert chain.outage_id == outage_id
        assert chain.payment_id == payment.id
        assert [n.entity_type for n in chain.nodes] == ["outage", "sla_result", "payment"]
    finally:
        for model in (PaymentTransactionORM, SLAResultORM, OutageORM):
            db.query(model).delete()
        db.commit()


def test_chain_resolves_outage_from_tx_hash(db):
    """tx_hash filter resolves to the payment, then the parent outage."""
    outage_id = _unique("outage")
    _make_outage(db, outage_id)
    sla = _make_sla_result(db, outage_id, BASE_TIME)
    tx_hash = _unique("tx")
    payment = _make_payment(db, outage_id, sla.id, BASE_TIME, tx_hash=tx_hash)
    db.commit()

    try:
        chain = build_trace_chain(db, tx_hash=tx_hash)
        assert chain.outage_id == outage_id
        assert chain.transaction_hash == tx_hash
        assert any(n.entity_type == "payment" and n.entity_id == payment.id for n in chain.nodes)
    finally:
        for model in (PaymentTransactionORM, SLAResultORM, OutageORM):
            db.query(model).delete()
        db.commit()


# ---------------------------------------------------------------------------
# Broken chain
# ---------------------------------------------------------------------------


def test_unknown_payment_id_returns_empty_chain(db):
    """A payment_id that matches no payment cannot resolve an outage."""
    chain = build_trace_chain(db, payment_id=_unique("missing-payment"))
    assert chain.total_nodes == 0
    assert chain.nodes == []
    assert chain.outage_id is None


def test_unknown_tx_hash_returns_empty_chain(db):
    """A transaction hash that matches no payment cannot resolve an outage."""
    chain = build_trace_chain(db, tx_hash=_unique("missing-tx"))
    assert chain.total_nodes == 0
    assert chain.nodes == []
    assert chain.outage_id is None


def test_missing_outage_record_returns_empty_chain(db):
    """A dangling outage_id (record deleted) yields an empty chain, not an error."""
    missing_outage_id = _unique("missing-outage")
    chain = build_trace_chain(db, outage_id=missing_outage_id)
    assert chain.total_nodes == 0
    assert chain.nodes == []
    # The requested outage_id is echoed back so callers can report the gap
    assert chain.outage_id == missing_outage_id


def test_outage_without_downstream_links_returns_only_outage_node(db):
    """A chain broken right after the outage still returns the outage node."""
    outage_id = _unique("outage")
    outage = _make_outage(db, outage_id)
    db.commit()

    try:
        chain = build_trace_chain(db, outage_id=outage_id)
        assert chain.total_nodes == 1
        assert chain.nodes[0].entity_type == "outage"
        assert chain.nodes[0].entity_id == outage.id
        assert chain.nodes[0].sequence == 1
    finally:
        db.query(OutageORM).delete()
        db.commit()


def test_webhook_deliveries_for_other_outages_or_broken_payloads_are_excluded(db):
    """Deliveries whose payload points elsewhere (or is malformed) are skipped."""
    outage_id = _unique("outage")
    other_outage_id = _unique("other-outage")
    _make_outage(db, outage_id)
    # Belongs to a different outage
    other_delivery = _make_webhook_delivery(
        db,
        json.dumps({"data": {"outage_id": other_outage_id}}),
        BASE_TIME,
    )
    # Malformed payloads must be skipped without raising
    broken_json_delivery = _make_webhook_delivery(db, "{not-json", BASE_TIME)
    missing_data_delivery = _make_webhook_delivery(db, json.dumps({"event": "sla.violation"}), BASE_TIME)
    db.commit()

    try:
        chain = build_trace_chain(db, outage_id=outage_id)
        webhook_ids = {
            n.entity_id for n in chain.nodes if n.entity_type == "webhook_delivery"
        }
        assert webhook_ids == set()
        assert str(other_delivery.id) not in webhook_ids
        assert str(broken_json_delivery.id) not in webhook_ids
        assert str(missing_data_delivery.id) not in webhook_ids
        # The outage node itself is still present
        assert [n.entity_type for n in chain.nodes] == ["outage"]
    finally:
        for model in (WebhookDelivery, Webhook, OutageORM):
            db.query(model).delete()
        db.commit()


# ---------------------------------------------------------------------------
# Empty chain
# ---------------------------------------------------------------------------


def test_no_filter_criteria_returns_empty_chain(db):
    """With no criteria, resolution is impossible and the chain is empty."""
    chain = build_trace_chain(db)
    assert chain.total_nodes == 0
    assert chain.nodes == []
    assert chain.outage_id is None
    assert chain.payment_id is None
    assert chain.transaction_hash is None


def test_empty_string_filters_treated_as_absent(db):
    """Empty-string filters behave like missing filters (falsy, not lookup keys)."""
    chain = build_trace_chain(db, outage_id="", payment_id="", tx_hash="")
    assert chain.total_nodes == 0
    assert chain.nodes == []
    assert chain.outage_id is None


# ---------------------------------------------------------------------------
# API boundary (GET /api/v1/audit/trace)
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_client(client):
    from app.core.security import require_admin
    from app.main import app

    app.dependency_overrides[require_admin] = lambda: {"user_id": "test", "role": "admin"}
    try:
        yield client
    finally:
        app.dependency_overrides.clear()


def test_trace_endpoint_returns_ordered_chain(admin_client, db):
    outage_id = _unique("outage")
    _make_outage(db, outage_id)
    sla = _make_sla_result(db, outage_id, BASE_TIME)
    payment = _make_payment(db, outage_id, sla.id, BASE_TIME)
    db.commit()

    try:
        response = admin_client.get("/api/v1/audit/trace", params={"outage_id": outage_id})
        assert response.status_code == 200
        body = response.json()
        assert body["outage_id"] == outage_id
        assert body["total_nodes"] == len(body["nodes"]) == 3
        assert [n["sequence"] for n in body["nodes"]] == [1, 2, 3]
        assert [n["entity_type"] for n in body["nodes"]] == ["outage", "sla_result", "payment"]
    finally:
        for model in (PaymentTransactionORM, SLAResultORM, OutageORM):
            db.query(model).delete()
        db.commit()


def test_trace_endpoint_requires_at_least_one_filter(admin_client):
    response = admin_client.get("/api/v1/audit/trace")
    assert response.status_code == 400
    assert "At least one filter parameter is required" in response.json()["detail"]


def test_trace_endpoint_returns_404_for_unresolvable_link(admin_client):
    response = admin_client.get(
        "/api/v1/audit/trace", params={"payment_id": _unique("missing-payment")}
    )
    assert response.status_code == 404
    assert "No trace chain found" in response.json()["detail"]