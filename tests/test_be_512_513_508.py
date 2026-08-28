import json
import time
from unittest.mock import AsyncMock, Mock, patch
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.webhook import Webhook, WebhookDelivery, WebhookDeliveryStatus, WebhookEvent


# --------------------------------------------------------------------------- #
# Issue #512: Webhook Delivery Payload Search Tests                           #
# --------------------------------------------------------------------------- #

def test_webhook_delivery_payload_search(client: TestClient, db: Session):
    # 1. Create a dummy Webhook
    webhook = Webhook(
        name="search-test-webhook",
        url="https://example.com/webhook",
        secret="secret",
        events="[\"sla.violation\"]",
    )
    db.add(webhook)
    db.commit()
    db.refresh(webhook)

    # 2. Create deliveries with varying JSON payloads
    payload_a = {"event": "alert", "details": {"source": "node-1", "severity": "critical"}}
    payload_b = {"event": "alert", "details": {"source": "node-2", "severity": "warning"}}
    payload_c = {"event": "resolve", "details": {"source": "node-1"}}

    delivery_a = WebhookDelivery(
        webhook_id=webhook.id,
        event=WebhookEvent.SLA_VIOLATION,
        payload=json.dumps(payload_a),
        status=WebhookDeliveryStatus.SUCCESS,
        attempt_count=1,
        idempotency_key="key-a",
        event_timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
    )
    delivery_b = WebhookDelivery(
        webhook_id=webhook.id,
        event=WebhookEvent.SLA_VIOLATION,
        payload=json.dumps(payload_b),
        status=WebhookDeliveryStatus.SUCCESS,
        attempt_count=1,
        idempotency_key="key-b",
        event_timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
    )
    delivery_c = WebhookDelivery(
        webhook_id=webhook.id,
        event=WebhookEvent.SLA_VIOLATION,
        payload=json.dumps(payload_c),
        status=WebhookDeliveryStatus.SUCCESS,
        attempt_count=1,
        idempotency_key="key-c",
        event_timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
    )
    db.add_all([delivery_a, delivery_b, delivery_c])
    db.commit()

    # Test Case 1: Search by event type
    response = client.post(
        "/api/v1/webhooks/deliveries/search",
        json={"matcher": {"event": "alert"}}
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    ids = {d["id"] for d in data}
    assert str(delivery_a.id) in ids
    assert str(delivery_b.id) in ids

    # Test Case 2: Search by nested source node-1
    response = client.post(
        "/api/v1/webhooks/deliveries/search",
        json={"matcher": {"details": {"source": "node-1"}}}
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    ids = {d["id"] for d in data}
    assert str(delivery_a.id) in ids
    assert str(delivery_c.id) in ids

    # Test Case 3: Search by nested severity critical
    response = client.post(
        "/api/v1/webhooks/deliveries/search",
        json={"matcher": {"details": {"severity": "critical"}}}
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["id"] == str(delivery_a.id)


# --------------------------------------------------------------------------- #
# Issue #513: Request Execution Latency Logging Middleware Tests             #
# --------------------------------------------------------------------------- #

def test_latency_logging_middleware_normal(client: TestClient, caplog: pytest.LogCaptureFixture):
    response = client.get("/health")
    assert response.status_code == 200
    
    # Verify logger recorded the request
    latency_logs = [r for r in caplog.records if r.name == "latency_middleware"]
    assert len(latency_logs) >= 1
    assert "Request completed" in latency_logs[0].message


@pytest.mark.asyncio
async def test_latency_middleware_slow_request():
    from app.middleware.latency import LatencyLoggingMiddleware
    
    middleware = LatencyLoggingMiddleware(app=Mock())
    request = Mock()
    request.method = "GET"
    request.url.path = "/slow-route"
    
    async def mock_call_next(req):
        time.sleep(0.52)  # sleep > 500ms
        response = Mock()
        response.status_code = 200
        return response
        
    with patch("app.middleware.latency.logger") as mock_logger:
        await middleware.dispatch(request, mock_call_next)
        mock_logger.warning.assert_called_once()
        args, kwargs = mock_logger.warning.call_args
        assert "Slow request detected" in args[0]
        assert kwargs["method"] == "GET"
        assert kwargs["path"] == "/slow-route"
        assert kwargs["status_code"] == 200
        assert kwargs["process_time_ms"] >= 500


# --------------------------------------------------------------------------- #
# Issue #508: Correlation ID Middleware Tests                                #
# --------------------------------------------------------------------------- #

def test_correlation_id_middleware_generates_id(client: TestClient):
    response = client.get("/health")
    assert response.status_code == 200
    
    # Header should be present in response
    assert "X-Correlation-ID" in response.headers
    correlation_id = response.headers["X-Correlation-ID"]
    
    # Should be a valid UUID v4
    val = UUID(correlation_id, version=4)
    assert val is not None


def test_correlation_id_middleware_preserves_id(client: TestClient):
    custom_id = "test-correlation-id-999"
    response = client.get("/health", headers={"X-Correlation-ID": custom_id})
    assert response.status_code == 200
    assert response.headers.get("X-Correlation-ID") == custom_id


@pytest.mark.asyncio
async def test_correlation_middleware_stores_contextvar_and_injects_request():
    from app.middleware.correlation import CorrelationMiddleware
    from app.utils.correlation import get_correlation_id
    
    middleware = CorrelationMiddleware(app=Mock())
    request = Mock()
    # Initial empty headers in scope
    request.scope = {"headers": []}
    request.headers = {}
    
    async def mock_call_next(req):
        # Inside the request scope, the context variable should be set
        assert get_correlation_id() is not None
        
        # The request headers scope should have been mutated to include x-correlation-id
        headers_dict = dict(req.scope["headers"])
        assert b"x-correlation-id" in headers_dict
        
        response = Mock()
        response.headers = {}
        response.body_iterator = AsyncMock()
        return response
        
    await middleware.dispatch(request, mock_call_next)
