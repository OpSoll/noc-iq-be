from datetime import datetime, timedelta
from unittest.mock import Mock, patch

import httpx
import pytest

from app.models.webhook import Webhook, WebhookDelivery, WebhookDeliveryStatus, WebhookEvent
from app.services.webhook_service import classify_http_status


def test_webhook_delivery_listing_supports_filters_and_total_count(client, db):
    webhook = Webhook(
        name="delivery-list-webhook",
        url="https://example.com/webhook",
        secret="secret",
        events="[\"sla.violation\"]",
    )
    db.add(webhook)
    db.commit()
    db.refresh(webhook)

    now = datetime.utcnow()
    successful_delivery = WebhookDelivery(
        webhook_id=webhook.id,
        event=WebhookEvent.SLA_VIOLATION,
        payload="{}",
        status=WebhookDeliveryStatus.SUCCESS,
        attempt_count=1,
        response_status_code=200,
        error_message=None,
        delivered_at=now - timedelta(minutes=10),
        created_at=now - timedelta(minutes=10),
    )
    failed_delivery = WebhookDelivery(
        webhook_id=webhook.id,
        event=WebhookEvent.SLA_VIOLATION,
        payload="{}",
        status=WebhookDeliveryStatus.FAILED,
        attempt_count=3,
        response_status_code=504,
        error_message="Request timed out",
        delivered_at=now - timedelta(minutes=5),
        created_at=now - timedelta(minutes=5),
    )
    pending_delivery = WebhookDelivery(
        webhook_id=webhook.id,
        event=WebhookEvent.SLA_VIOLATION,
        payload="{}",
        status=WebhookDeliveryStatus.PENDING,
        attempt_count=0,
        response_status_code=None,
        error_message=None,
        delivered_at=None,
        created_at=now - timedelta(minutes=1),
    )
    db.add_all([successful_delivery, failed_delivery, pending_delivery])
    db.commit()

    response = client.get(
        f"/webhooks/{webhook.id}/deliveries",
        params={
            "status": "failed",
            "event": "sla.violation",
            "search": "timed out",
            "delivered_after": (now - timedelta(minutes=6)).isoformat(),
            "delivered_before": (now - timedelta(minutes=4)).isoformat(),
            "limit": 10,
            "offset": 0,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["limit"] == 10
    assert data["offset"] == 0
    assert data["returned"] == 1
    assert data["has_more"] is False
    assert data["items"][0]["status"] == "failed"
    assert data["items"][0]["error_message"] == "Request timed out"


def test_webhook_delivery_listing_pagination_metadata(client, db):
    webhook = Webhook(
        name="delivery-paging-webhook",
        url="https://example.com/webhook",
        secret="secret",
        events="[\"sla.violation\"]",
    )
    db.add(webhook)
    db.commit()
    db.refresh(webhook)

    base = datetime.utcnow()
    deliveries = []
    for i in range(3):
        deliveries.append(
            WebhookDelivery(
                webhook_id=webhook.id,
                event=WebhookEvent.SLA_VIOLATION,
                payload="{}",
                status=WebhookDeliveryStatus.FAILED,
                attempt_count=i + 1,
                response_status_code=500,
                error_message=f"failure-{i}",
                delivered_at=base - timedelta(minutes=i),
                created_at=base - timedelta(minutes=i),
            )
        )
    db.add_all(deliveries)
    db.commit()

    response = client.get(
        f"/webhooks/{webhook.id}/deliveries",
        params={"status": "failed", "limit": 1, "offset": 0},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 3
    assert data["returned"] == 1
    assert data["has_more"] is True
    assert data["offset"] == 0
    assert data["limit"] == 1

    response_next = client.get(
        f"/webhooks/{webhook.id}/deliveries",
        params={"status": "failed", "limit": 1, "offset": 1},
    )
    assert response_next.status_code == 200
    data_next = response_next.json()
    assert data_next["total"] == 3
    assert data_next["returned"] == 1
    assert data_next["has_more"] is True
    assert data_next["offset"] == 1
    assert data_next["limit"] == 1
    assert data_next["items"][0]["error_message"] == "failure-1"


def test_http_status_code_classification():
    """Test that HTTP status codes are correctly classified as retryable or terminal."""
    # 2xx - terminal (success)
    assert classify_http_status(200) == "terminal"
    assert classify_http_status(201) == "terminal"
    assert classify_http_status(204) == "terminal"
    
    # 3xx - terminal (redirection)
    assert classify_http_status(301) == "terminal"
    assert classify_http_status(302) == "terminal"
    assert classify_http_status(308) == "terminal"
    
    # 4xx - terminal (client error)
    assert classify_http_status(400) == "terminal"
    assert classify_http_status(401) == "terminal"
    assert classify_http_status(403) == "terminal"
    assert classify_http_status(404) == "terminal"
    assert classify_http_status(429) == "terminal"
    
    # 5xx - retryable (server error)
    assert classify_http_status(500) == "retryable"
    assert classify_http_status(502) == "retryable"
    assert classify_http_status(503) == "retryable"
    assert classify_http_status(504) == "retryable"


def test_webhook_delivery_terminal_status_dead_letters_immediately(client, db):
    """Test that terminal status codes (3xx/4xx) dead-letter immediately without retry."""
    webhook = Webhook(
        name="terminal-test-webhook",
        url="https://example.com/webhook",
        secret="secret",
        events="[\"sla.violation\"]",
        max_retries=3,
    )
    db.add(webhook)
    db.commit()
    db.refresh(webhook)

    delivery = WebhookDelivery(
        webhook_id=webhook.id,
        event=WebhookEvent.SLA_VIOLATION,
        payload="{}",
        status=WebhookDeliveryStatus.PENDING,
        attempt_count=0,
    )
    db.add(delivery)
    db.commit()
    db.refresh(delivery)

    # Mock HTTP response with 404 (terminal)
    mock_response = Mock()
    mock_response.status_code = 404
    mock_response.text = "Not Found"
    mock_response.is_success = False

    with patch("httpx.Client") as mock_client:
        mock_client.return_value.__enter__.return_value.post.return_value = mock_response
        
        from app.services.webhook_service import dispatch_delivery
        dispatch_delivery(db, delivery.id)

    db.refresh(delivery)
    
    # Should be dead-lettered immediately (no retry)
    assert delivery.status == WebhookDeliveryStatus.DEAD_LETTER
    assert delivery.attempt_count == 1
    assert delivery.response_status_code == 404
    assert delivery.dead_lettered_at is not None
    assert delivery.next_retry_at is None


def test_webhook_delivery_retryable_status_schedules_retry(client, db):
    """Test that retryable status codes (5xx) schedule retry with exponential backoff."""
    webhook = Webhook(
        name="retryable-test-webhook",
        url="https://example.com/webhook",
        secret="secret",
        events="[\"sla.violation\"]",
        max_retries=3,
    )
    db.add(webhook)
    db.commit()
    db.refresh(webhook)

    delivery = WebhookDelivery(
        webhook_id=webhook.id,
        event=WebhookEvent.SLA_VIOLATION,
        payload="{}",
        status=WebhookDeliveryStatus.PENDING,
        attempt_count=0,
    )
    db.add(delivery)
    db.commit()
    db.refresh(delivery)

    # Mock HTTP response with 503 (retryable)
    mock_response = Mock()
    mock_response.status_code = 503
    mock_response.text = "Service Unavailable"
    mock_response.is_success = False

    with patch("httpx.Client") as mock_client:
        mock_client.return_value.__enter__.return_value.post.return_value = mock_response
        
        from app.services.webhook_service import dispatch_delivery
        dispatch_delivery(db, delivery.id)

    db.refresh(delivery)
    
    # Should be scheduled for retry
    assert delivery.status == WebhookDeliveryStatus.RETRYING
    assert delivery.attempt_count == 1
    assert delivery.response_status_code == 503
    assert delivery.next_retry_at is not None
    assert delivery.dead_lettered_at is None


def test_webhook_delivery_2xx_success_no_retry(client, db):
    """Test that 2xx status codes succeed without retry."""
    webhook = Webhook(
        name="success-test-webhook",
        url="https://example.com/webhook",
        secret="secret",
        events="[\"sla.violation\"]",
        max_retries=3,
    )
    db.add(webhook)
    db.commit()
    db.refresh(webhook)

    delivery = WebhookDelivery(
        webhook_id=webhook.id,
        event=WebhookEvent.SLA_VIOLATION,
        payload="{}",
        status=WebhookDeliveryStatus.PENDING,
        attempt_count=0,
    )
    db.add(delivery)
    db.commit()
    db.refresh(delivery)

    # Mock HTTP response with 200 (success)
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.text = "OK"
    mock_response.is_success = True

    with patch("httpx.Client") as mock_client:
        mock_client.return_value.__enter__.return_value.post.return_value = mock_response
        
        from app.services.webhook_service import dispatch_delivery
        dispatch_delivery(db, delivery.id)

    db.refresh(delivery)
    
    # Should succeed immediately
    assert delivery.status == WebhookDeliveryStatus.SUCCESS
    assert delivery.attempt_count == 1
    assert delivery.response_status_code == 200
    assert delivery.delivered_at is not None
    assert delivery.next_retry_at is None
    assert delivery.dead_lettered_at is None


def test_webhook_metadata_endpoint_exposes_policy(client):
    """Test that the metadata endpoint exposes retryable/terminal status codes."""
    response = client.get("/webhooks/metadata")
    
    assert response.status_code == 200
    data = response.json()
    
    # Check structure
    assert "retryable_status_codes" in data
    assert "terminal_status_codes" in data
    assert "retry_policy" in data
    assert "schema_version" in data
    
    # Check retryable codes include 5xx
    assert 500 in data["retryable_status_codes"]
    assert 502 in data["retryable_status_codes"]
    assert 503 in data["retryable_status_codes"]
    assert 504 in data["retryable_status_codes"]
    
    # Check terminal codes include 2xx, 3xx, 4xx
    assert 200 in data["terminal_status_codes"]
    assert 404 in data["terminal_status_codes"]
    assert 301 in data["terminal_status_codes"]
    
    # Check retry policy
    assert data["retry_policy"]["max_retries"] == 3
    assert "base_delays_seconds" in data["retry_policy"]
    assert "max_delay_seconds" in data["retry_policy"]
    
    # Check schema version
    assert data["schema_version"] == "1"
