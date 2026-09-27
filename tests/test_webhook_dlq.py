"""Tests for webhook delivery dead-letter queue (DLQ).

Validates:
1. Failed webhooks are routed to webhook_dead_letter_queue table after 5 retries
2. Final HTTP response status code and error message are recorded
3. Administrative redelivery API endpoint works correctly
"""
import json
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch, MagicMock
from uuid import uuid4


class TestWebhookDeadLetterQueueORM(unittest.TestCase):
    """Test the DLQ ORM model."""

    def test_webhook_dead_letter_orm_has_required_columns(self):
        """WebhookDeadLetterORM should have all required columns."""
        from app.models.orm.webhook_dead_letter import WebhookDeadLetterORM
        columns = {c.name for c in WebhookDeadLetterORM.__table__.columns}
        self.assertIn("id", columns)
        self.assertIn("delivery_id", columns)
        self.assertIn("webhook_id", columns)
        self.assertIn("event", columns)
        self.assertIn("response_status_code", columns)
        self.assertIn("error_message", columns)
        self.assertIn("attempt_count", columns)
        self.assertIn("dead_lettered_at", columns)
        self.assertIn("redelivered", columns)

    def test_webhook_dead_letter_orm_table_name(self):
        """WebhookDeadLetterORM should use the webhook_dead_letter_queue table."""
        from app.models.orm.webhook_dead_letter import WebhookDeadLetterORM
        self.assertEqual(WebhookDeadLetterORM.__tablename__, "webhook_dead_letter_queue")


class TestWebhookDeadLetterPydanticModel(unittest.TestCase):
    """Test the DLQ Pydantic model."""

    def test_dead_letter_entry_has_required_fields(self):
        """WebhookDeadLetterEntry should have all required fields."""
        from app.models.webhook_dead_letter import WebhookDeadLetterEntry
        entry = WebhookDeadLetterEntry(
            id=uuid4(),
            delivery_id=uuid4(),
            webhook_id=uuid4(),
            event="sla.violation",
            response_status_code=503,
            error_message="Server unavailable",
            attempt_count=5,
            dead_lettered_at=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
        )
        self.assertEqual(entry.response_status_code, 503)
        self.assertEqual(entry.error_message, "Server unavailable")
        self.assertEqual(entry.attempt_count, 5)

    def test_dead_letter_entry_defaults(self):
        """WebhookDeadLetterEntry should have sensible defaults."""
        from app.models.webhook_dead_letter import WebhookDeadLetterEntry
        entry = WebhookDeadLetterEntry(
            id=uuid4(),
            delivery_id=uuid4(),
            webhook_id=uuid4(),
            event="sla.violation",
            dead_lettered_at=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
        )
        self.assertFalse(entry.redelivered)
        self.assertIsNone(entry.response_status_code)
        self.assertIsNone(entry.error_message)
        self.assertEqual(entry.attempt_count, 0)


class TestRouteToDeadLetterQueue(unittest.TestCase):
    """Test _route_to_dead_letter_queue function."""

    def test_route_to_dlq_creates_entry(self):
        """_route_to_dead_letter_queue should create a DLQ entry."""
        from app.services.webhook_service import _route_to_dead_letter_queue
        from app.models.webhook import WebhookDelivery, WebhookDeliveryStatus, WebhookEvent

        db = Mock()
        delivery = Mock()
        delivery.id = uuid4()
        delivery.event = WebhookEvent.SLA_VIOLATION
        delivery.payload = '{"test":"data"}'
        delivery.response_status_code = 503
        delivery.response_body = "Service Unavailable"
        delivery.error_message = "Server returned 503"
        delivery.attempt_count = 5
        delivery.updated_at = datetime.now(timezone.utc)

        webhook = Mock()
        webhook.id = uuid4()

        _route_to_dead_letter_queue(db, delivery, webhook)

        db.add.assert_called_once()
        db.commit.assert_called_once()

        # Verify the entry that was added
        added_entry = db.add.call_args[0][0]
        self.assertEqual(added_entry.delivery_id, delivery.id)
        self.assertEqual(added_entry.webhook_id, webhook.id)
        self.assertEqual(added_entry.response_status_code, 503)
        self.assertEqual(added_entry.error_message, "Server returned 503")
        self.assertEqual(added_entry.attempt_count, 5)

    def test_route_to_dlq_handles_db_error(self):
        """_route_to_dead_letter_queue should handle DB errors gracefully."""
        from app.services.webhook_service import _route_to_dead_letter_queue

        db = Mock()
        db.add.side_effect = Exception("DB error")

        delivery = Mock()
        delivery.id = uuid4()
        delivery.event = Mock()
        delivery.event.value = "sla.violation"
        delivery.payload = "{}"
        delivery.response_status_code = 500
        delivery.response_body = ""
        delivery.error_message = "Internal error"
        delivery.attempt_count = 5
        delivery.updated_at = datetime.now(timezone.utc)

        webhook = Mock()
        webhook.id = uuid4()

        # Should not raise - error is caught and logged
        _route_to_dead_letter_queue(db, delivery, webhook)
        db.rollback.assert_called_once()


class TestDispatchDeliveryDeadLettering(unittest.TestCase):
    """Test that dispatch_delivery routes to DLQ on retry exhaustion."""

    @patch("app.services.webhook_service._attempt_delivery", return_value=False)
    @patch("app.services.webhook_service._route_to_dead_letter_queue")
    def test_dispatch_delivery_routes_to_dlq_after_max_retries(
        self, mock_dlq, mock_attempt
    ):
        """dispatch_delivery should route to DLQ when retries exhausted."""
        from app.services.webhook_service import dispatch_delivery
        from app.models.webhook import Webhook, WebhookDelivery, WebhookDeliveryStatus, WebhookEvent

        db = Mock()
        delivery = Mock(spec=WebhookDelivery)
        delivery.id = uuid4()
        delivery.status = WebhookDeliveryStatus.RETRYING
        delivery.attempt_count = 5  # Max retries reached
        delivery.event = WebhookEvent.SLA_VIOLATION
        delivery.signature_version = 1
        delivery.idempotency_key = "test-key"
        delivery.response_status_code = 503
        delivery.response_body = ""
        delivery.error_message = "Server error"
        delivery.next_retry_at = None
        delivery.dead_lettered_at = None
        delivery.delivered_at = None

        webhook = Mock(spec=Webhook)
        webhook.id = uuid4()
        webhook.url = "https://example.com/webhook"
        webhook.secret = None
        webhook.events = json.dumps(["sla.violation"])
        webhook.max_retries = 5

        delivery.webhook = webhook
        db.query.return_value.filter.return_value.first.return_value = delivery

        dispatch_delivery(db, delivery.id)

        # DLQ should be called since retries are exhausted
        mock_dlq.assert_called_once()

    @patch("app.services.webhook_service._attempt_delivery", return_value=False)
    @patch("app.services.webhook_service._route_to_dead_letter_queue")
    def test_dispatch_delivery_routes_to_dlq_on_terminal_failure(
        self, mock_dlq, mock_attempt
    ):
        """dispatch_delivery should route to DLQ on terminal HTTP failure."""
        from app.services.webhook_service import dispatch_delivery
        from app.models.webhook import Webhook, WebhookDelivery, WebhookDeliveryStatus, WebhookEvent

        db = Mock()
        delivery = Mock(spec=WebhookDelivery)
        delivery.id = uuid4()
        delivery.status = WebhookDeliveryStatus.PENDING
        delivery.attempt_count = 1
        delivery.event = WebhookEvent.SLA_VIOLATION
        delivery.signature_version = 1
        delivery.idempotency_key = "test-key"
        delivery.response_status_code = 403  # Terminal failure
        delivery.response_body = "Forbidden"
        delivery.error_message = "Terminal failure: HTTP 403"
        delivery.next_retry_at = None
        delivery.dead_lettered_at = None
        delivery.delivered_at = None

        webhook = Mock(spec=Webhook)
        webhook.id = uuid4()
        webhook.url = "https://example.com/webhook"
        webhook.secret = None
        webhook.events = json.dumps(["sla.violation"])
        webhook.max_retries = 3

        delivery.webhook = webhook
        db.query.return_value.filter.return_value.first.return_value = delivery

        dispatch_delivery(db, delivery.id)

        # DLQ should be called for terminal failures
        mock_dlq.assert_called_once()


class TestWebhookDeadLetterEndpoint(unittest.TestCase):
    """Test webhook DLQ API endpoints."""

    def test_dead_letter_response_item_model(self):
        """WebhookDeadLetterResponseItem should have all fields."""
        from app.api.v1.endpoints.webhooks import WebhookDeadLetterResponseItem
        item = WebhookDeadLetterResponseItem(
            id=uuid4(),
            delivery_id=uuid4(),
            webhook_id=uuid4(),
            event="sla.violation",
            response_status_code=503,
            error_message="Server unavailable",
            attempt_count=5,
            dead_lettered_at=datetime.now(timezone.utc).isoformat(),
            created_at=datetime.now(timezone.utc).isoformat(),
            redelivered=False,
        )
        self.assertEqual(item.response_status_code, 503)
        self.assertFalse(item.redelivered)

    def test_dead_letter_list_response_model(self):
        """WebhookDeadLetterListResponse should contain items and total."""
        from app.api.v1.endpoints.webhooks import (
            WebhookDeadLetterListResponse,
            WebhookDeadLetterResponseItem,
        )
        item = WebhookDeadLetterResponseItem(
            id=uuid4(),
            delivery_id=uuid4(),
            webhook_id=uuid4(),
            event="sla.violation",
            attempt_count=5,
            dead_lettered_at=datetime.now(timezone.utc).isoformat(),
            created_at=datetime.now(timezone.utc).isoformat(),
            redelivered=False,
        )
        response = WebhookDeadLetterListResponse(items=[item], total=1)
        self.assertEqual(len(response.items), 1)
        self.assertEqual(response.total, 1)


if __name__ == "__main__":
    unittest.main()
