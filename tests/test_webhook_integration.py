import asyncio
import json
from unittest.mock import patch

import pytest
from httpx import AsyncClient

from app.core.config import settings
from app.models.webhook import Webhook
from app.services.webhook import WebhookService
from tests.mocks.webhook_receiver import MockWebhookReceiver


@pytest.fixture(scope="module")
async def webhook_receiver():
    receiver = MockWebhookReceiver()
    # Run the server in a separate task
    server_task = asyncio.create_task(receiver.start())
    yield receiver
    # Stop the server and wait for the task to complete
    receiver.stop()
    await server_task


@pytest.mark.asyncio
async def test_webhook_dispatch_and_signature_verification(async_client: AsyncClient, webhook_receiver: MockWebhookReceiver):
    webhook_secret = "test_secret"
    webhook_url = "http://127.0.0.1:8001/"

    # Create a webhook
    webhook = Webhook(url=webhook_url, secret=webhook_secret)

    # Dispatch a test event
    event_payload = {"event": "test_event", "data": {"foo": "bar"}}

    with patch.object(settings, "WEBHOOK_SECRET", webhook_secret):
        webhook_service = WebhookService()
        await webhook_service.dispatch_event(webhook, event_payload)

    # Allow some time for the webhook to be processed
    await asyncio.sleep(0.1)

    # Verify the received request
    received_requests = webhook_receiver.get_requests()
    assert len(received_requests) == 1

    headers, body = received_requests[0]
    assert "x-webhook-signature" in headers

    # Verify the signature
    expected_signature = webhook_service._generate_signature(body, webhook_secret)
    assert headers["x-webhook-signature"] == expected_signature

    # Verify the payload
    assert json.loads(body) == event_payload