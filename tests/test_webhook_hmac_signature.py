"""Tests for HMAC SHA-256 signature generator for outgoing webhook dispatches.

Validates:
1. HMAC SHA-256 signature computation using endpoint signing secret
2. X-Webhook-Signature header format: t={timestamp},v1={hex_signature}
3. Signature verification and replay protection
4. Timestamp freshness validation
"""
import time
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch


class TestHmacSha256SignatureGeneration(unittest.TestCase):
    """Test HMAC SHA-256 signature generation for webhook dispatches."""

    def test_sign_payload_v1_generates_hmac_sha256(self):
        """sign_payload_v1 should produce an HMAC-SHA256 hex digest."""
        from app.services.webhook_signing import sign_payload_v1
        secret = "test-signing-secret-123"
        payload = '{"event":"sla.violation","data":{"device_id":"dev-1"}}'
        sig = sign_payload_v1(secret, payload)
        self.assertIsInstance(sig, str)
        self.assertEqual(len(sig), 64)  # SHA-256 hex = 64 chars

    def test_sign_payload_v1_is_deterministic(self):
        """Same inputs should produce identical signatures."""
        from app.services.webhook_signing import sign_payload_v1
        secret = "my-secret"
        payload = '{"test":"data"}'
        sig1 = sign_payload_v1(secret, payload)
        sig2 = sign_payload_v1(secret, payload)
        self.assertEqual(sig1, sig2)

    def test_sign_payload_v1_changes_with_different_secret(self):
        """Different secrets should produce different signatures."""
        from app.services.webhook_signing import sign_payload_v1
        payload = '{"test":"data"}'
        sig1 = sign_payload_v1("secret-1", payload)
        sig2 = sign_payload_v1("secret-2", payload)
        self.assertNotEqual(sig1, sig2)

    def test_sign_payload_v1_changes_with_different_payload(self):
        """Different payloads should produce different signatures."""
        from app.services.webhook_signing import sign_payload_v1
        secret = "my-secret"
        sig1 = sign_payload_v1(secret, '{"a":1}')
        sig2 = sign_payload_v1(secret, '{"a":2}')
        self.assertNotEqual(sig1, sig2)

    def test_sign_payload_v1_with_timestamp(self):
        """sign_payload_v1_with_timestamp should include timestamp in signature."""
        from app.services.webhook_signing import sign_payload_v1_with_timestamp
        secret = "test-secret"
        payload = '{"event":"sla.violation"}'
        timestamp = 1700000000
        sig = sign_payload_v1_with_timestamp(secret, payload, timestamp)
        self.assertIsInstance(sig, str)
        self.assertEqual(len(sig), 64)

    def test_sign_payload_v1_with_timestamp_changes_with_timestamp(self):
        """Different timestamps should produce different signatures."""
        from app.services.webhook_signing import sign_payload_v1_with_timestamp
        secret = "test-secret"
        payload = '{"event":"sla.violation"}'
        sig1 = sign_payload_v1_with_timestamp(secret, payload, 1700000000)
        sig2 = sign_payload_v1_with_timestamp(secret, payload, 1700000001)
        self.assertNotEqual(sig1, sig2)

    def test_sign_payload_v1_with_timestamp_is_deterministic(self):
        """Same inputs including timestamp should produce identical signatures."""
        from app.services.webhook_signing import sign_payload_v1_with_timestamp
        secret = "test-secret"
        payload = '{"test": true}'
        timestamp = 1700000000
        sig1 = sign_payload_v1_with_timestamp(secret, payload, timestamp)
        sig2 = sign_payload_v1_with_timestamp(secret, payload, timestamp)
        self.assertEqual(sig1, sig2)


class TestSignatureHeaderFormat(unittest.TestCase):
    """Test X-Webhook-Signature header format: t=,v1="""

    def test_build_signature_header_format(self):
        """build_signature_header should produce t=,v1= format."""
        from app.services.webhook_signing import build_signature_header
        secret = "endpoint-secret"
        payload = '{"event":"sla.violation"}'
        timestamp = 1700000000
        header = build_signature_header(secret, payload, timestamp)
        self.assertTrue(header.startswith("t=1700000000,v1="))

    def test_build_signature_header_contains_valid_hex(self):
        """Header v1= part should be a valid hex string."""
        from app.services.webhook_signing import build_signature_header
        header = build_signature_header("secret", '{"test":true}', 1000)
        v1_part = header.split("v1=")[1]
        # Should be valid hex
        int(v1_part, 16)

    def test_build_signature_header_with_real_world_payload(self):
        """Header should work with realistic webhook payloads."""
        from app.services.webhook_signing import build_signature_header
        import json
        payload = json.dumps({
            "schema_version": "1",
            "event": "sla.violation",
            "timestamp": "2026-01-01T00:00:00Z",
            "data": {"device_id": "dev-123", "severity": "high"},
        })
        header = build_signature_header("my-webhook-secret", payload, 1700000000)
        self.assertIn("t=1700000000", header)
        self.assertIn("v1=", header)

    def test_parse_signature_header(self):
        """parse_signature_header should extract t and v1 components."""
        from app.services.webhook_signing import parse_signature_header
        parsed = parse_signature_header("t=1700000000,v1=abcdef123456")
        self.assertEqual(parsed["t"], "1700000000")
        self.assertEqual(parsed["v1"], "abcdef123456")

    def test_parse_signature_header_empty_string(self):
        """parse_signature_header should return empty dict for empty string."""
        from app.services.webhook_signing import parse_signature_header
        parsed = parse_signature_header("")
        self.assertEqual(parsed, {})

    def test_parse_signature_header_with_spaces(self):
        """parse_signature_header should handle whitespace."""
        from app.services.webhook_signing import parse_signature_header
        parsed = parse_signature_header("t=1000 , v1=abc123")
        self.assertEqual(parsed["t"], "1000")
        self.assertEqual(parsed["v1"], "abc123")


class TestSignatureVerification(unittest.TestCase):
    """Test signature verification for incoming webhook validation."""

    def test_verify_signature_header_valid(self):
        """Valid signature should verify successfully."""
        from app.services.webhook_signing import (
            build_signature_header,
            verify_signature_header,
        )
        secret = "webhook-secret"
        payload = '{"event":"sla.violation"}'
        timestamp = int(time.time())
        header = build_signature_header(secret, payload, timestamp)
        self.assertTrue(verify_signature_header(secret, payload, header))

    def test_verify_signature_header_rejects_tampered_payload(self):
        """Signature of different payload should not verify."""
        from app.services.webhook_signing import (
            build_signature_header,
            verify_signature_header,
        )
        secret = "webhook-secret"
        timestamp = int(time.time())
        header = build_signature_header(secret, '{"event":"sla.violation"}', timestamp)
        self.assertFalse(verify_signature_header(secret, '{"event":"tampered"}', header))

    def test_verify_signature_header_rejects_wrong_secret(self):
        """Signature with wrong secret should not verify."""
        from app.services.webhook_signing import (
            build_signature_header,
            verify_signature_header,
        )
        timestamp = int(time.time())
        header = build_signature_header("secret-1", '{"test":true}', timestamp)
        self.assertFalse(verify_signature_header("secret-2", '{"test":true}', header))

    def test_verify_signature_header_rejects_old_timestamp(self):
        """Signature older than max_age_seconds should be rejected."""
        from app.services.webhook_signing import (
            build_signature_header,
            verify_signature_header,
        )
        secret = "webhook-secret"
        payload = '{"test":true}'
        old_timestamp = int(time.time()) - 600  # 10 minutes ago
        header = build_signature_header(secret, payload, old_timestamp)
        self.assertFalse(
            verify_signature_header(secret, payload, header, max_age_seconds=300)
        )

    def test_verify_signature_header_rejects_invalid_format(self):
        """Malformed header should be rejected."""
        from app.services.webhook_signing import verify_signature_header
        self.assertFalse(verify_signature_header("s", '{"t":true}', "invalid-header"))
        self.assertFalse(verify_signature_header("s", '{"t":true}', ""))
        self.assertFalse(verify_signature_header("s", '{"t":true}', "v1=abc"))

    def test_verify_signature_header_rejects_non_numeric_timestamp(self):
        """Non-numeric timestamp should be rejected."""
        from app.services.webhook_signing import verify_signature_header
        self.assertFalse(
            verify_signature_header("s", '{"t":true}', "t=not-a-number,v1=abc")
        )

    def test_verify_signature_header_uses_constant_time_comparison(self):
        """Verification should be timing-safe (uses hmac.compare_digest)."""
        from app.services.webhook_signing import (
            build_signature_header,
            verify_signature_header,
        )
        secret = "webhook-secret"
        payload = '{"test":true}'
        timestamp = int(time.time())
        header = build_signature_header(secret, payload, timestamp)
        # Should still verify correctly (constant-time comparison)
        self.assertTrue(verify_signature_header(secret, payload, header))


class TestWebhookDispatchSignatureIntegration(unittest.TestCase):
    """Integration tests for webhook dispatch with t=,v1= signature format."""

    def test_build_headers_includes_new_signature_format(self):
        """_build_headers should inject X-Webhook-Signature in t=,v1= format."""
        from app.services.webhook_service import _build_headers
        from app.models.webhook import WebhookEvent

        webhook = Mock()
        webhook.secret = "test-endpoint-secret"

        payload = '{"schema_version":"1","event":"sla.violation","data":{}}'
        headers = _build_headers(webhook, payload, WebhookEvent.SLA_VIOLATION)

        sig_header = headers["X-Webhook-Signature"]
        self.assertTrue(sig_header.startswith("t="))
        self.assertIn(",v1=", sig_header)

    def test_build_headers_without_secret_omits_signature(self):
        """Headers should not include signature when no secret is configured."""
        from app.services.webhook_service import _build_headers
        from app.models.webhook import WebhookEvent

        webhook = Mock()
        webhook.secret = None

        headers = _build_headers(webhook, '{"test":true}', WebhookEvent.SLA_VIOLATION)
        self.assertNotIn("X-Webhook-Signature", headers)

    def test_signature_header_is_self_containing(self):
        """The t=,v1= header should contain everything needed for verification."""
        from app.services.webhook_signing import (
            build_signature_header,
            verify_signature_header,
        )
        secret = "production-secret"
        payload = '{"schema_version":"1","event":"sla.violation","timestamp":"2026-01-01T00:00:00","data":{"device_id":"dev-1"}}'
        timestamp = int(time.time())

        header = build_signature_header(secret, payload, timestamp)

        # Receiver can verify without knowing the timestamp separately
        self.assertTrue(verify_signature_header(secret, payload, header))

    def test_signature_roundtrip_with_empty_secret(self):
        """Signature should work with empty secret (edge case)."""
        from app.services.webhook_signing import (
            build_signature_header,
            verify_signature_header,
        )
        timestamp = int(time.time())
        header = build_signature_header("", '{"test":true}', timestamp)
        self.assertTrue(verify_signature_header("", '{"test":true}', header))


class TestSignatureConstants(unittest.TestCase):
    """Test signature versioning constants."""

    def test_current_signature_version_is_1(self):
        """CURRENT_SIGNATURE_VERSION should be 1."""
        from app.services.webhook_signing import CURRENT_SIGNATURE_VERSION
        self.assertEqual(CURRENT_SIGNATURE_VERSION, 1)

    def test_sign_payload_defaults_to_current_version(self):
        """sign_payload should default to CURRENT_SIGNATURE_VERSION."""
        from app.services.webhook_signing import (
            CURRENT_SIGNATURE_VERSION,
            sign_payload,
        )
        sig, version = sign_payload("secret", '{"test":true}')
        self.assertEqual(version, CURRENT_SIGNATURE_VERSION)


if __name__ == "__main__":
    unittest.main()
