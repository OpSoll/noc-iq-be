"""Tests for the GIN-indexed JSONB payload search service (search_webhook_payloads).

Covers:
- Search by subscriber_id in nested payload
- Search by outage_id in nested payload
- Empty query raises ValueError
- Pagination with limit/offset
- Fallback to in-memory scan on SQLite
- Total count returned correctly
"""
import json
from datetime import datetime, timedelta

import pytest

from app.models.webhook import Webhook, WebhookDelivery, WebhookDeliveryStatus, WebhookEvent
from app.services.webhook_service import search_webhook_payloads, _json_contains


def _make_delivery(db, webhook, payload_dict, created_at=None):
    """Helper to create a WebhookDelivery with a JSON payload."""
    import uuid
    now = created_at or datetime.utcnow()
    delivery = WebhookDelivery(
        webhook_id=webhook.id,
        event=WebhookEvent.SLA_VIOLATION,
        payload=json.dumps(payload_dict),
        status=WebhookDeliveryStatus.SUCCESS,
        attempt_count=1,
        response_status_code=200,
        idempotency_key=f"key-{uuid.uuid4()}",
        event_timestamp=now,
        created_at=now,
    )
    db.add(delivery)
    db.commit()
    db.refresh(delivery)
    return delivery


def test_search_by_subscriber_id(client, db):
    """Search by subscriber_id in nested payload data."""
    webhook = Webhook(
        name="search-test-webhook",
        url="https://example.com/webhook",
        secret="secret",
        events='["sla.violation"]',
    )
    db.add(webhook)
    db.commit()
    db.refresh(webhook)

    now = datetime.utcnow()
    _make_delivery(db, webhook, {
        "schema_version": "1",
        "event": "sla.violation",
        "data": {"subscriber_id": "sub-abc-123", "outage_id": "out-001"},
    }, created_at=now - timedelta(minutes=10))
    _make_delivery(db, webhook, {
        "schema_version": "1",
        "event": "sla.violation",
        "data": {"subscriber_id": "sub-xyz-999", "outage_id": "out-002"},
    }, created_at=now - timedelta(minutes=5))

    result = search_webhook_payloads(db, {"data": {"subscriber_id": "sub-abc-123"}})

    assert result["total"] == 1
    assert len(result["items"]) == 1
    payload = json.loads(result["items"][0].payload)
    assert payload["data"]["subscriber_id"] == "sub-abc-123"


def test_search_by_outage_id(client, db):
    """Search by outage_id in nested payload data."""
    webhook = Webhook(
        name="search-outage-webhook",
        url="https://example.com/webhook",
        secret="secret",
        events='["sla.violation"]',
    )
    db.add(webhook)
    db.commit()
    db.refresh(webhook)

    now = datetime.utcnow()
    _make_delivery(db, webhook, {
        "schema_version": "1",
        "data": {"subscriber_id": "sub-001", "outage_id": "out-999"},
    }, created_at=now - timedelta(minutes=10))
    _make_delivery(db, webhook, {
        "schema_version": "1",
        "data": {"subscriber_id": "sub-002", "outage_id": "out-111"},
    }, created_at=now - timedelta(minutes=5))

    result = search_webhook_payloads(db, {"data": {"outage_id": "out-999"}})

    assert result["total"] == 1
    assert len(result["items"]) == 1
    payload = json.loads(result["items"][0].payload)
    assert payload["data"]["outage_id"] == "out-999"


def test_search_empty_query_raises_value_error(client, db):
    """Empty query_dict raises ValueError."""
    with pytest.raises(ValueError, match="must not be empty"):
        search_webhook_payloads(db, {})


def test_search_pagination_with_limit_offset(client, db):
    """Search results are paginated correctly."""
    webhook = Webhook(
        name="search-pagination-webhook",
        url="https://example.com/webhook",
        secret="secret",
        events='["sla.violation"]',
    )
    db.add(webhook)
    db.commit()
    db.refresh(webhook)

    now = datetime.utcnow()
    # Create 5 deliveries with same subscriber_id
    for i in range(5):
        _make_delivery(db, webhook, {
            "schema_version": "1",
            "data": {"subscriber_id": "sub-batch-001", "index": i},
        }, created_at=now - timedelta(minutes=5 - i))

    # First page
    result = search_webhook_payloads(db, {"data": {"subscriber_id": "sub-batch-001"}}, limit=2, offset=0)
    assert result["total"] == 5
    assert len(result["items"]) == 2
    assert result["limit"] == 2
    assert result["offset"] == 0

    # Second page
    result2 = search_webhook_payloads(db, {"data": {"subscriber_id": "sub-batch-001"}}, limit=2, offset=2)
    assert result2["total"] == 5
    assert len(result2["items"]) == 2
    assert result2["offset"] == 2

    # Third page (partial)
    result3 = search_webhook_payloads(db, {"data": {"subscriber_id": "sub-batch-001"}}, limit=2, offset=4)
    assert result3["total"] == 5
    assert len(result3["items"]) == 1


def test_search_no_matches(client, db):
    """Search returns empty when no deliveries match."""
    webhook = Webhook(
        name="search-empty-webhook",
        url="https://example.com/webhook",
        secret="secret",
        events='["sla.violation"]',
    )
    db.add(webhook)
    db.commit()
    db.refresh(webhook)

    _make_delivery(db, webhook, {
        "schema_version": "1",
        "data": {"subscriber_id": "sub-001"},
    })

    result = search_webhook_payloads(db, {"data": {"subscriber_id": "sub-nonexistent"}})
    assert result["total"] == 0
    assert len(result["items"]) == 0


def test_json_contains_basic():
    """_json_contains correctly checks containment."""
    target = {"a": 1, "b": {"c": 2, "d": 3}}
    assert _json_contains(target, {"a": 1}) is True
    assert _json_contains(target, {"b": {"c": 2}}) is True
    assert _json_contains(target, {"b": {"c": 99}}) is False
    assert _json_contains(target, {"e": 1}) is False


def test_json_contains_nested():
    """_json_contains handles nested structures."""
    target = {"data": {"subscriber_id": "sub-001", "tags": ["a", "b"]}}
    assert _json_contains(target, {"data": {"subscriber_id": "sub-001"}}) is True
    assert _json_contains(target, {"data": {"subscriber_id": "sub-999"}}) is False


def test_json_contains_list():
    """_json_contains handles list containment."""
    target = {"items": [{"id": 1}, {"id": 2}]}
    assert _json_contains(target, {"items": [{"id": 1}]}) is True
    assert _json_contains(target, {"items": [{"id": 3}]}) is False


def test_search_multiple_fields(client, db):
    """Search by multiple fields simultaneously."""
    webhook = Webhook(
        name="search-multi-webhook",
        url="https://example.com/webhook",
        secret="secret",
        events='["sla.violation"]',
    )
    db.add(webhook)
    db.commit()
    db.refresh(webhook)

    now = datetime.utcnow()
    _make_delivery(db, webhook, {
        "schema_version": "1",
        "data": {"subscriber_id": "sub-001", "outage_id": "out-001", "severity": "critical"},
    }, created_at=now - timedelta(minutes=10))
    _make_delivery(db, webhook, {
        "schema_version": "1",
        "data": {"subscriber_id": "sub-001", "outage_id": "out-002", "severity": "warning"},
    }, created_at=now - timedelta(minutes=5))

    # Search by subscriber_id AND outage_id
    result = search_webhook_payloads(db, {
        "data": {"subscriber_id": "sub-001", "outage_id": "out-001"}
    })
    assert result["total"] == 1
    payload = json.loads(result["items"][0].payload)
    assert payload["data"]["outage_id"] == "out-001"
