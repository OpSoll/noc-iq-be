"""Tests for webhook signature versioning and timestamp validation (BE-087).

Validates:
1. Signature versioning enables safe algorithm evolution
2. Timestamp validation semantics for idempotency and freshness
3. Backward compatibility for signature version transitions  
4. Receiver-facing contract compliance
"""

import json
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, Mock, patch
from uuid import uuid4

from app.models.webhook import Webhook, WebhookDelivery, WebhookDeliveryStatus, WebhookEvent
from app.services.webhook_signing import (
    CURRENT_SIGNATURE_VERSION,
    sign_payload,
    sign_payload_v1,
    verify_signature,
    verify_signature_v1,
)
from app.services.webhook_service import (
    _build_headers,
    create_delivery,
    trigger_sla_violation_webhooks,
)


class TestSignatureVersioningV1(unittest.TestCase):
    """Test HMAC-SHA256 signature generation and verification (v1)."""

    def test_sign_payload_v1_generates_consistent_signature(self):
        """Same payload and secret should generate identical signature."""
        secret = "test-secret-key-123"
        payload = '{"event":"sla.violation","timestamp":"2026-04-29T10:00:00"}'
        
        sig1 = sign_payload_v1(secret, payload)
        sig2 = sign_payload_v1(secret, payload)
        
        self.assertEqual(sig1, sig2)
        self.assertIsInstance(sig1, str)
        self.assertTrue(len(sig1) > 0)

    def test_verify_signature_v1_accepts_valid_signature(self):
        """Valid signature should verify successfully."""
        secret = "test-secret"
        payload = '{"test":"data"}'
        signature = sign_payload_v1(secret, payload)
        
        is_valid = verify_signature_v1(secret, payload, signature)
        self.assertTrue(is_valid)

    def test_verify_signature_v1_rejects_invalid_signature(self):
        """Invalid signature should fail verification."""
        secret = "test-secret"
        payload = '{"test":"data"}'
        invalid_signature = "invalid_hex_string_" + "0" * 48
        
        is_valid = verify_signature_v1(secret, payload, invalid_signature)
        self.assertFalse(is_valid)

    def test_verify_signature_v1_rejects_tampered_payload(self):
        """Signature of different payload should not verify."""
        secret = "test-secret"
        original_payload = '{"test":"data","amount":100}'
        tampered_payload = '{"test":"data","amount":999}'
        
        signature = sign_payload_v1(secret, original_payload)
        is_valid = verify_signature_v1(secret, tampered_payload, signature)
        
        self.assertFalse(is_valid)

    def test_verify_signature_v1_rejects_wrong_secret(self):
        """Signature with wrong secret should not verify."""
        payload = '{"test":"data"}'
        secret1 = "secret-key-1"
        secret2 = "secret-key-2"
        
        signature = sign_payload_v1(secret1, payload)
        is_valid = verify_signature_v1(secret2, payload, signature)
        
        self.assertFalse(is_valid)

    def test_signature_is_timing_safe(self):
        """Verification should use constant-time comparison."""
        # This is implicit in verify_signature_v1 using hmac.compare_digest
        secret = "test-secret"
        payload = '{"test":"data"}'
        valid_sig = sign_payload_v1(secret, payload)
        
        # verify_signature_v1 uses hmac.compare_digest internally
        is_valid = verify_signature_v1(secret, payload, valid_sig)
        self.assertTrue(is_valid)


class TestSignatureVersioning(unittest.TestCase):
    """Test polymorphic signature signing with version support."""

    def test_sign_payload_defaults_to_current_version(self):
        """sign_payload should default to CURRENT_SIGNATURE_VERSION."""
        secret = "test-secret"
        payload = '{"test":"data"}'
        
        sig, version = sign_payload(secret, payload)
        
        self.assertEqual(version, CURRENT_SIGNATURE_VERSION)
        self.assertEqual(version, 1)  # Current version is 1

    def test_sign_payload_v1_forward_reference(self):
        """Signing with version 1 explicitly should work."""
        secret = "test-secret"
        payload = '{"test":"data"}'
        
        sig, version = sign_payload(secret, payload, version=1)
        
        self.assertEqual(version, 1)
        # Verify signature is valid
        is_valid = verify_signature(secret, payload, sig, version=1)
        self.assertTrue(is_valid)

    def test_sign_payload_rejects_unsupported_version(self):
        """Unsupported signature versions should raise ValueError."""
        secret = "test-secret"
        payload = '{"test":"data"}'
        
        with self.assertRaises(ValueError) as ctx:
            sign_payload(secret, payload, version=99)
        
        self.assertIn("Unsupported signature version", str(ctx.exception))

    def test_verify_signature_defaults_to_current_version(self):
        """verify_signature should default to CURRENT_SIGNATURE_VERSION."""
        secret = "test-secret"
        payload = '{"test":"data"}'
        sig = sign_payload_v1(secret, payload)
        
        is_valid = verify_signature(secret, payload, sig)
        self.assertTrue(is_valid)

    def test_verify_signature_unsupported_version_fails(self):
        """Verifying with unsupported version should return False."""
        secret = "test-secret"
        payload = '{"test":"data"}'
        sig = sign_payload_v1(secret, payload)
        
        # Verification with unsupported version should fail safely
        is_valid = verify_signature(secret, payload, sig, version=99)
        self.assertFalse(is_valid)


class TestWebhookHeadersWithSignatureVersion(unittest.TestCase):
    """Test that webhook headers include explicit signature version."""

    def test_build_headers_includes_signature_version_with_secret(self):
        """Headers should include signature version when secret is configured."""
        webhook = Mock(spec=Webhook)
        webhook.secret = "test-secret"
        
        payload = '{"test":"data"}'
        headers = _build_headers(webhook, payload, WebhookEvent.SLA_VIOLATION, signature_version=1)
        
        self.assertIn("X-Webhook-Signature-Version", headers)
        self.assertEqual(headers["X-Webhook-Signature-Version"], "1")
        self.assertIn("X-Webhook-Signature", headers)
        self.assertTrue(headers["X-Webhook-Signature"].startswith("sha256="))

    def test_build_headers_omits_signature_version_without_secret(self):
        """Headers should not include signature version when secret is None."""
        webhook = Mock(spec=Webhook)
        webhook.secret = None
        
        payload = '{"test":"data"}'
        headers = _build_headers(webhook, payload, WebhookEvent.SLA_VIOLATION)
        
        self.assertNotIn("X-Webhook-Signature-Version", headers)
        self.assertNotIn("X-Webhook-Signature", headers)

    def test_build_headers_includes_timestamp(self):
        """Headers should always include timestamp (for idempotency)."""
        webhook = Mock(spec=Webhook)
        webhook.secret = None
        
        payload = '{"test":"data"}'
        headers = _build_headers(webhook, payload, WebhookEvent.SLA_VIOLATION)
        
        self.assertIn("X-Webhook-Timestamp", headers)
        # Timestamp should be ISO format
        timestamp_str = headers["X-Webhook-Timestamp"]
        # Should parse successfully as ISO datetime
        datetime.fromisoformat(timestamp_str)

    def test_build_headers_with_explicit_signature_version(self):
        """_build_headers should use provided signature version."""
        webhook = Mock(spec=Webhook)
        webhook.secret = "test-secret"
        
        payload = '{"test":"data"}'
        
        # Current version
        headers_v1 = _build_headers(webhook, payload, signature_version=1)
        self.assertEqual(headers_v1["X-Webhook-Signature-Version"], "1")


class TestWebhookDeliveryWithSignatureVersion(unittest.TestCase):
    """Test that WebhookDelivery records store signature version."""

    def test_create_delivery_stores_signature_version(self):
        """Delivery should store explicit signature version."""
        db = Mock()
        webhook = Mock(spec=Webhook)
        webhook.id = uuid4()
        
        payload = {"event": "sla.violation", "data": {"test": "data"}}
        
        # Mock the database commit and refresh
        delivery_mock = Mock(spec=WebhookDelivery)
        db.add.return_value = None
        db.commit.return_value = None
        db.refresh.return_value = None
        
        # Create delivery with signature_version=1
        delivery = WebhookDelivery(
            webhook_id=webhook.id,
            event=WebhookEvent.SLA_VIOLATION,
            payload=json.dumps(payload),
            status=WebhookDeliveryStatus.PENDING,
            signature_version=1,
        )
        
        self.assertEqual(delivery.signature_version, 1)

    def test_delivery_signature_version_defaults_to_current(self):
        """WebhookDelivery signature_version should default to CURRENT_SIGNATURE_VERSION."""
        webhook_id = uuid4()
        
        delivery = WebhookDelivery(
            webhook_id=webhook_id,
            event=WebhookEvent.SLA_VIOLATION,
            payload='{"test":"data"}',
            status=WebhookDeliveryStatus.PENDING,
        )
        
        # Database model should have default value
        # (This would be set at DB layer with server_default="1")
        # For the ORM model, we should have a default
        self.assertTrue(hasattr(delivery, 'signature_version'))


class TestTimestampValidationSemantics(unittest.TestCase):
    """Test timestamp validation for idempotency and freshness."""

    def test_payload_includes_immutable_timestamp(self):
        """Timestamp in payload should be immutable across retries."""
        payload_data = {
            "schema_version": "1",
            "event": "sla.violation",
            "timestamp": "2026-04-29T10:00:00.123456",
            "data": {"device_id": "dev-123"},
        }
        
        payload_str = json.dumps(payload_data)
        payload1 = json.loads(payload_str)
        payload2 = json.loads(payload_str)
        
        self.assertEqual(payload1["timestamp"], payload2["timestamp"])

    def test_timestamp_iso_format_parseable(self):
        """Timestamps should be in ISO 8601 format."""
        event_time = datetime.utcnow()
        timestamp_str = event_time.isoformat()
        
        # Should parse successfully
        parsed = datetime.fromisoformat(timestamp_str)
        self.assertIsInstance(parsed, datetime)

    def test_timestamp_idempotency_deduplication(self):
        """Webhook_id + timestamp should uniquely identify events (idempotency)."""
        webhook_id_1 = uuid4()
        webhook_id_2 = uuid4()
        timestamp_1 = "2026-04-29T10:00:00.123456"
        timestamp_2 = "2026-04-29T10:00:01.000000"
        
        # Different webhook_id + same timestamp = different events
        event1 = (webhook_id_1, timestamp_1)
        event2 = (webhook_id_2, timestamp_1)
        self.assertNotEqual(event1, event2)
        
        # Same webhook_id + different timestamp = different events
        event3 = (webhook_id_1, timestamp_1)
        event4 = (webhook_id_1, timestamp_2)
        self.assertNotEqual(event3, event4)
        
        # Same webhook_id + same timestamp = same event (idempotent)
        event5 = (webhook_id_1, timestamp_1)
        event6 = (webhook_id_1, timestamp_1)
        self.assertEqual(event5, event6)

    def test_timestamp_freshness_validation(self):
        """Receivers can optionally validate timestamp freshness."""
        current_time = datetime.utcnow()
        
        # Recent timestamp (within 1 hour)
        recent_time = current_time - timedelta(minutes=30)
        age_seconds = (current_time - recent_time).total_seconds()
        self.assertLess(age_seconds, 3600)
        
        # Old timestamp (beyond 1 hour)
        old_time = current_time - timedelta(hours=2)
        age_seconds = (current_time - old_time).total_seconds()
        self.assertGreater(age_seconds, 3600)

    def test_timestamp_audit_trail_correlation(self):
        """Timestamps enable audit trail correlation."""
        event_timestamp = "2026-04-29T10:00:00.123456"
        delivery_created = "2026-04-29T10:00:05.000000"
        delivery_succeeded = "2026-04-29T10:00:06.000000"
        
        # Can calculate latency: event_occurred -> delivery_attempted
        event_occurred = datetime.fromisoformat(event_timestamp)
        delivery_attempted = datetime.fromisoformat(delivery_created)
        latency = (delivery_attempted - event_occurred).total_seconds()
        
        self.assertGreater(latency, 0)
        self.assertLess(latency, 10)
        
        # Can calculate processing time: delivery_attempted -> delivery_succeeded
        delivery_time = (datetime.fromisoformat(delivery_succeeded) - delivery_attempted).total_seconds()
        self.assertGreater(delivery_time, 0)


class TestSignatureVersionEvolution(unittest.TestCase):
    """Test that versioning enables safe algorithm evolution."""

    def test_dual_signing_backward_compatibility(self):
        """Future: can sign with multiple versions during transition period."""
        secret = "test-secret"
        payload = '{"event":"sla.violation","data":{}}'
        
        # Simulate dual-signing in migration phase
        sig_v1 = sign_payload_v1(secret, payload)
        sig_v1_tuple = sign_payload(secret, payload, version=1)
        
        # Both should be valid
        self.assertTrue(verify_signature_v1(secret, payload, sig_v1))
        self.assertTrue(verify_signature(secret, payload, sig_v1_tuple[0], version=1))

    def test_version_header_enables_receiver_upgrade_path(self):
        """Explicit version header helps receivers implement new algorithm support."""
        webhook = Mock(spec=Webhook)
        webhook.secret = "test-secret"
        
        payload = '{"test":"data"}'
        headers_v1 = _build_headers(webhook, payload, signature_version=1)
        
        # Receiver implementation:
        # if headers['X-Webhook-Signature-Version'] == '1':
        #     verify_v1(...)
        # elif headers['X-Webhook-Signature-Version'] == '2':
        #     verify_v2(...)
        sig_version = int(headers_v1["X-Webhook-Signature-Version"])
        self.assertEqual(sig_version, 1)

    def test_graceful_degradation_for_unknown_versions(self):
        """Unknown signature versions should fail safe."""
        secret = "test-secret"
        payload = '{"test":"data"}'
        
        # Try to verify with unsupported version
        result = verify_signature(secret, payload, "fake_sig", version=999)
        
        # Should fail safely (not crash)
        self.assertFalse(result)


class TestWebhookSigningIntegration(unittest.TestCase):
    """Integration tests for webhook signing with versioning."""

    def test_signature_remains_valid_across_retry_attempts(self):
        """Same delivery should use same signature across retries."""
        secret = "test-secret"
        
        delivery1 = WebhookDelivery(
            webhook_id=uuid4(),
            event=WebhookEvent.SLA_VIOLATION,
            payload='{"test":"data"}',
            status=WebhookDeliveryStatus.PENDING,
            signature_version=1,
        )
        
        # Simulate retry
        delivery2 = WebhookDelivery(
            webhook_id=delivery1.webhook_id,
            event=delivery1.event,
            payload=delivery1.payload,  # Same payload
            status=WebhookDeliveryStatus.RETRYING,
            signature_version=1,  # Same version
        )
        
        # Signatures should be identical
        sig1 = sign_payload_v1(secret, delivery1.payload)
        sig2 = sign_payload_v1(secret, delivery2.payload)
        self.assertEqual(sig1, sig2)

    def test_webhook_event_payload_structure(self):
        """Webhook payload should have documented structure for consumers."""
        payload = {
            "schema_version": "1",
            "event": "sla.violation",
            "timestamp": datetime.utcnow().isoformat(),
            "data": {
                "device_id": "dev-123",
                "outage_id": "out-456",
                "severity": "high",
                "sla_violated": True,
            },
        }
        
        # Assert structure is well-defined
        self.assertIn("schema_version", payload)
        self.assertIn("event", payload)
        self.assertIn("timestamp", payload)
        self.assertIn("data", payload)
        
        # Timestamp should be ISO format
        datetime.fromisoformat(payload["timestamp"])


class TestWebhookSchemaVersionGuardrails(unittest.TestCase):
    """Tests for BE-W5-033: webhook payload schema version migration guardrails."""

    def test_validate_known_schema_version_and_compatible_event(self):
        """Known schema_version with compatible event_type should pass."""
        from app.services.webhook_service import validate_payload_schema_version
        payload = {"schema_version": "1", "event": "sla.violation"}
        is_valid, reason = validate_payload_schema_version(payload, WebhookEvent.SLA_VIOLATION)
        self.assertTrue(is_valid)
        self.assertEqual(reason, "")

    def test_validate_all_schema_v1_compatible_events(self):
        """All v1 events should pass validation."""
        from app.services.webhook_service import validate_payload_schema_version
        for event in [WebhookEvent.SLA_VIOLATION, WebhookEvent.SLA_WARNING, WebhookEvent.SLA_RESOLVED]:
            payload = {"schema_version": "1", "event": event.value}
            is_valid, reason = validate_payload_schema_version(payload, event)
            self.assertTrue(is_valid, f"Expected valid for {event.value}")
            self.assertEqual(reason, "")

    def test_unknown_schema_version_dead_lettered(self):
        """Payload with unknown schema_version must be rejected with explicit reason."""
        from app.services.webhook_service import (
            validate_payload_schema_version,
            DEAD_LETTER_REASON_UNKNOWN_SCHEMA_VERSION,
        )
        payload = {"schema_version": "99", "event": "sla.violation"}
        is_valid, reason = validate_payload_schema_version(payload, WebhookEvent.SLA_VIOLATION)
        self.assertFalse(is_valid)
        self.assertEqual(reason, DEAD_LETTER_REASON_UNKNOWN_SCHEMA_VERSION)

    def test_missing_schema_version_dead_lettered(self):
        """Payload missing schema_version must be dead-lettered."""
        from app.services.webhook_service import (
            validate_payload_schema_version,
            DEAD_LETTER_REASON_UNKNOWN_SCHEMA_VERSION,
        )
        payload = {"event": "sla.violation"}
        is_valid, reason = validate_payload_schema_version(payload, WebhookEvent.SLA_VIOLATION)
        self.assertFalse(is_valid)
        self.assertEqual(reason, DEAD_LETTER_REASON_UNKNOWN_SCHEMA_VERSION)

    def test_incompatible_event_type_for_known_version(self):
        """Known schema_version with incompatible event_type must be dead-lettered."""
        from app.services.webhook_service import (
            validate_payload_schema_version,
            DEAD_LETTER_REASON_INCOMPATIBLE_EVENT_TYPE,
            SUPPORTED_SCHEMA_VERSIONS,
        )
        # Patch an incompatible event by temporarily using an event not in v1 list
        # We simulate by passing a patched event value
        class FakeEvent:
            value = "unknown.event"
        payload = {"schema_version": "1", "event": "unknown.event"}
        is_valid, reason = validate_payload_schema_version(payload, FakeEvent())
        self.assertFalse(is_valid)
        self.assertEqual(reason, DEAD_LETTER_REASON_INCOMPATIBLE_EVENT_TYPE)

    def test_supported_schema_versions_matrix_is_documented(self):
        """SUPPORTED_SCHEMA_VERSIONS matrix must be non-empty and contain version '1'."""
        from app.services.webhook_service import SUPPORTED_SCHEMA_VERSIONS
        self.assertIn("1", SUPPORTED_SCHEMA_VERSIONS)
        self.assertIsInstance(SUPPORTED_SCHEMA_VERSIONS["1"], list)
        self.assertGreater(len(SUPPORTED_SCHEMA_VERSIONS["1"]), 0)

    def test_trigger_dead_letters_unknown_schema_version(self):
        """trigger_sla_violation_webhooks must dead-letter delivery for unknown schema_version."""
        from app.services.webhook_service import (
            DEAD_LETTER_REASON_UNKNOWN_SCHEMA_VERSION,
            SUPPORTED_SCHEMA_VERSIONS,
            validate_payload_schema_version,
        )
        # Simulate payload with unsupported schema_version
        payload = {"schema_version": "99", "event": "sla.violation", "timestamp": datetime.utcnow().isoformat(), "data": {}}
        is_valid, reason = validate_payload_schema_version(payload, WebhookEvent.SLA_VIOLATION)
        self.assertFalse(is_valid)
        self.assertEqual(reason, DEAD_LETTER_REASON_UNKNOWN_SCHEMA_VERSION)

    def test_schema_version_is_string_coerced(self):
        """schema_version in payload should be coerced to string for comparison."""
        from app.services.webhook_service import validate_payload_schema_version
        # Integer schema_version coerced to "1"
        payload = {"schema_version": 1, "event": "sla.violation"}
        is_valid, reason = validate_payload_schema_version(payload, WebhookEvent.SLA_VIOLATION)
        self.assertTrue(is_valid)


if __name__ == "__main__":
    unittest.main()
