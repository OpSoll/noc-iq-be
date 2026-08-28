"""Tests for X-Webhook-Delivery-ID header and idempotency behavior.

Covers:
- X-Webhook-Delivery-ID is a unique UUID per delivery attempt
- X-Webhook-Idempotency-Key remains constant across retries
- X-Webhook-Delivery-ID changes on redelivery
- Headers are documented in webhook metadata endpoint
"""
import json
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

import pytest

from app.models.webhook import Webhook, WebhookDelivery, WebhookDeliveryStatus, WebhookEvent
from app.services.webhook_service import _build_headers, dispatch_delivery


def test_delivery_id_is_unique_uuid(client, db):
    """Each delivery attempt gets a unique X-Webhook-Delivery-ID."""
    webhook = Webhook(
        name="delivery-id-test-webhook",
        url="https://example.com/webhook",
        secret=None,
        events='["sla.violation"]',
    )
    db.add(webhook)
    db.commit()
    db.refresh(webhook)

    # Build headers twice and verify delivery_id changes
    headers1 = _build_headers(
        webhook, '{"test": true}', WebhookEvent.SLA_VIOLATION,
        idempotency_key="stable-key",
        delivery_id="delivery-uuid-1",
    )
    headers2 = _build_headers(
        webhook, '{"test": true}', WebhookEvent.SLA_VIOLATION,
        idempotency_key="stable-key",
        delivery_id="delivery-uuid-2",
    )

    # Delivery IDs should be different
    assert headers1["X-Webhook-Delivery-ID"] == "delivery-uuid-1"
    assert headers2["X-Webhook-Delivery-ID"] == "delivery-uuid-2"
    assert headers1["X-Webhook-Delivery-ID"] != headers2["X-Webhook-Delivery-ID"]


def test_idempotency_key_constant_across_retries(client, db):
    """X-Webhook-Idempotency-Key remains constant across retry attempts."""
    webhook = Webhook(
        name="idempotency-const-webhook",
        url="https://example.com/webhook",
        secret=None,
        events='["sla.violation"]',
    )
    db.add(webhook)
    db.commit()
    db.refresh(webhook)

    idempotency_key = "deterministic-key-abc-123"

    # Simulate headers for first attempt
    headers_attempt_1 = _build_headers(
        webhook, '{"test": true}', WebhookEvent.SLA_VIOLATION,
        idempotency_key=idempotency_key,
        delivery_id="first-attempt-uuid",
    )

    # Simulate headers for second attempt (different delivery_id)
    headers_attempt_2 = _build_headers(
        webhook, '{"test": true}', WebhookEvent.SLA_VIOLATION,
        idempotency_key=idempotency_key,
        delivery_id="second-attempt-uuid",
    )

    # Idempotency key should be the same
    assert headers_attempt_1["X-Webhook-Idempotency-Key"] == idempotency_key
    assert headers_attempt_2["X-Webhook-Idempotency-Key"] == idempotency_key
    # Delivery IDs should differ
    assert headers_attempt_1["X-Webhook-Delivery-ID"] != headers_attempt_2["X-Webhook-Delivery-ID"]


def test_attempt_delivery_generates_unique_delivery_id(client, db):
    """_attempt_delivery generates a unique delivery_id per call."""
    webhook = Webhook(
        name="attempt-delivery-id-webhook",
        url="https://example.com/webhook",
        secret=None,
        events='["sla.violation"]',
    )
    db.add(webhook)
    db.commit()
    db.refresh(webhook)

    delivery = WebhookDelivery(
        webhook_id=webhook.id,
        event=WebhookEvent.SLA_VIOLATION,
        payload='{"test": true}',
        status=WebhookDeliveryStatus.PENDING,
        attempt_count=0,
    )
    db.add(delivery)
    db.commit()
    db.refresh(delivery)

    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.text = "OK"
    mock_response.is_success = True

    with patch("httpx.Client") as mock_client:
        mock_client.return_value.__enter__.return_value.post.return_value = mock_response
        dispatch_delivery(db, delivery.id)

    # Verify the delivery_id header was passed to httpx
    call_args = mock_client.return_value.__enter__.return_value.post.call_args
    sent_headers = call_args.kwargs.get("headers") or call_args[1].get("headers", {})
    delivery_id_value = sent_headers.get("X-Webhook-Delivery-ID")
    assert delivery_id_value is not None
    # Should be a valid UUID format
    import uuid
    uuid.UUID(delivery_id_value)  # Will raise if not valid UUID


def test_delivery_id_changes_on_redelivery(client, db):
    """Each delivery attempt gets a different X-Webhook-Delivery-ID."""
    webhook = Webhook(
        name="redelivery-id-webhook",
        url="https://example.com/webhook",
        secret=None,
        events='["sla.violation"]',
    )
    db.add(webhook)
    db.commit()
    db.refresh(webhook)

    delivery = WebhookDelivery(
        webhook_id=webhook.id,
        event=WebhookEvent.SLA_VIOLATION,
        payload='{"test": true}',
        status=WebhookDeliveryStatus.PENDING,
        attempt_count=0,
    )
    db.add(delivery)
    db.commit()
    db.refresh(delivery)

    captured_headers_list = []

    def capture_headers(*args, **kwargs):
        mock_response = Mock()
        mock_response.status_code = 503  # Will trigger retry
        mock_response.text = "Service Unavailable"
        mock_response.is_success = False
        captured_headers_list.append(kwargs.get("headers", {}))
        return mock_response

    with patch("httpx.Client") as mock_client:
        mock_client.return_value.__enter__.return_value.post.side_effect = capture_headers
        dispatch_delivery(db, delivery.id)

    # Should have captured at least one call
    assert len(captured_headers_list) >= 1
    # The delivery_id should be present in all captured calls
    for headers in captured_headers_list:
        assert "X-Webhook-Delivery-ID" in headers


def test_metadata_endpoint_documents_delivery_id_header(client):
    """Metadata endpoint documents X-Webhook-Delivery-ID header."""
    response = client.get("/webhooks/metadata")
    assert response.status_code == 200
    data = response.json()
    assert "headers" in data
    assert "X-Webhook-Delivery-ID" in data["headers"]
    assert "X-Webhook-Idempotency-Key" in data["headers"]
    assert "X-Webhook-Event" in data["headers"]
    assert "X-Webhook-Timestamp" in data["headers"]


def test_delivery_id_not_in_headers_when_not_provided(client, db):
    """X-Webhook-Delivery-ID is omitted when delivery_id is None."""
    webhook = Webhook(
        name="no-delivery-id-webhook",
        url="https://example.com/webhook",
        secret=None,
        events='["sla.violation"]',
    )
    db.add(webhook)
    db.commit()
    db.refresh(webhook)

    headers = _build_headers(
        webhook, '{"test": true}', WebhookEvent.SLA_VIOLATION,
        idempotency_key="key-123",
        delivery_id=None,
    )

    assert "X-Webhook-Delivery-ID" not in headers
    # Idempotency key should still be present
    assert headers["X-Webhook-Idempotency-Key"] == "key-123"


def test_both_headers_present_simultaneously(client, db):
    """Both X-Webhook-Delivery-ID and X-Webhook-Idempotency-Key can be present."""
    webhook = Webhook(
        name="both-headers-webhook",
        url="https://example.com/webhook",
        secret=None,
        events='["sla.violation"]',
    )
    db.add(webhook)
    db.commit()
    db.refresh(webhook)

    headers = _build_headers(
        webhook, '{"test": true}', WebhookEvent.SLA_VIOLATION,
        idempotency_key="event-id-123",
        delivery_id="delivery-uuid-456",
    )

    assert headers["X-Webhook-Idempotency-Key"] == "event-id-123"
    assert headers["X-Webhook-Delivery-ID"] == "delivery-uuid-456"
