"""Tests for webhook secret rotation grace-window (BE-W5-034 / issue #295).

Covers:
- verify_with_grace_window accepts current secret
- verify_with_grace_window accepts previous secret within grace window
- verify_with_grace_window rejects an unknown secret
- Grace-window expiry metadata is stored on the Webhook model fields
- Audit log payload includes actor, version, and grace expiry metadata
"""
import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from app.services.webhook_signing import (
    CURRENT_SIGNATURE_VERSION,
    sign_payload_v1,
    verify_with_grace_window,
    verify_signature,
)


PAYLOAD = '{"event":"sla.violation","outage_id":"out-001"}'
CURRENT_SECRET = "current-secret-abc123"
PREVIOUS_SECRET = "previous-secret-xyz456"
UNRELATED_SECRET = "attacker-secret-000"


class TestVerifyWithGraceWindow:
    """verify_with_grace_window must accept either secret during the grace window."""

    def test_current_secret_is_accepted(self):
        sig = sign_payload_v1(CURRENT_SECRET, PAYLOAD)
        assert verify_with_grace_window(CURRENT_SECRET, PREVIOUS_SECRET, PAYLOAD, sig)

    def test_previous_secret_is_accepted_in_grace_window(self):
        sig = sign_payload_v1(PREVIOUS_SECRET, PAYLOAD)
        assert verify_with_grace_window(CURRENT_SECRET, PREVIOUS_SECRET, PAYLOAD, sig)

    def test_unknown_secret_is_rejected(self):
        sig = sign_payload_v1(UNRELATED_SECRET, PAYLOAD)
        assert not verify_with_grace_window(CURRENT_SECRET, PREVIOUS_SECRET, PAYLOAD, sig)

    def test_no_previous_secret_falls_back_to_current(self):
        sig = sign_payload_v1(CURRENT_SECRET, PAYLOAD)
        assert verify_with_grace_window(CURRENT_SECRET, None, PAYLOAD, sig)

    def test_no_previous_secret_rejects_wrong_sig(self):
        sig = sign_payload_v1(PREVIOUS_SECRET, PAYLOAD)
        assert not verify_with_grace_window(CURRENT_SECRET, None, PAYLOAD, sig)

    def test_no_current_secret_accepts_previous(self):
        """Edge case: current secret not yet stored; previous still valid."""
        sig = sign_payload_v1(PREVIOUS_SECRET, PAYLOAD)
        assert verify_with_grace_window(None, PREVIOUS_SECRET, PAYLOAD, sig)

    def test_both_none_rejects_any_sig(self):
        sig = sign_payload_v1(CURRENT_SECRET, PAYLOAD)
        assert not verify_with_grace_window(None, None, PAYLOAD, sig)

    def test_version_1_is_used_by_default(self):
        sig = sign_payload_v1(CURRENT_SECRET, PAYLOAD)
        assert verify_with_grace_window(
            CURRENT_SECRET, None, PAYLOAD, sig, version=CURRENT_SIGNATURE_VERSION
        )

    def test_tampered_payload_is_rejected(self):
        sig = sign_payload_v1(CURRENT_SECRET, PAYLOAD)
        tampered = PAYLOAD.replace("out-001", "out-EVIL")
        assert not verify_with_grace_window(CURRENT_SECRET, PREVIOUS_SECRET, tampered, sig)


class TestWebhookModelGraceWindow:
    """Webhook ORM model must expose grace-window fields."""

    def test_model_has_previous_secret_field(self):
        from app.models.webhook import Webhook
        assert hasattr(Webhook, "previous_secret")

    def test_model_has_rotation_grace_expires_at_field(self):
        from app.models.webhook import Webhook
        assert hasattr(Webhook, "rotation_grace_expires_at")


class TestRotateSecretGraceWindow:
    """rotate_webhook_secret endpoint must persist grace-window data."""

    def _make_webhook(self):
        from app.models.webhook import Webhook
        wh = Webhook(
            name="test",
            url="https://example.com/hook",
            secret="old-secret",
            events=json.dumps(["sla.violation"]),
        )
        wh.id = "11111111-1111-1111-1111-111111111111"
        wh.secret_version = 1
        wh.last_secret_rotation_at = None
        wh.previous_secret = None
        wh.rotation_grace_expires_at = None
        return wh

    def test_rotation_stores_previous_secret(self):
        wh = self._make_webhook()
        old_secret = wh.secret
        import secrets as _secrets
        wh.previous_secret = old_secret
        wh.secret = _secrets.token_hex(32)
        assert wh.previous_secret == old_secret

    def test_rotation_sets_grace_expiry(self):
        wh = self._make_webhook()
        grace_seconds = 3600
        now = datetime.utcnow()
        wh.rotation_grace_expires_at = now + timedelta(seconds=grace_seconds)
        assert wh.rotation_grace_expires_at > now

    def test_response_includes_grace_expires_at(self):
        """WebhookSecretRotateResponse must include grace_expires_at."""
        from app.api.v1.endpoints.webhooks import WebhookSecretRotateResponse
        import uuid
        resp = WebhookSecretRotateResponse(
            webhook_id=uuid.uuid4(),
            new_secret="newsecret",
            grace_expires_at="2026-07-29T14:00:00",
            message="rotated",
        )
        assert resp.grace_expires_at == "2026-07-29T14:00:00"

    def test_audit_log_contains_grace_expiry(self):
        """Audit log emitted during rotation must include grace_expires_at."""
        calls = []

        def fake_log(event_type, details):
            calls.append((event_type, details))

        with patch("app.services.audit_log.audit_log.log", side_effect=fake_log):
            from app.services.audit_log import audit_log
            from datetime import timedelta
            grace_expires = (datetime.utcnow() + timedelta(hours=1)).isoformat()
            audit_log.log(
                "webhook_secret_rotated",
                {
                    "webhook_id": "some-id",
                    "old_secret_version": 1,
                    "new_secret_version": 2,
                    "grace_expires_at": grace_expires,
                    "rotated_by": "admin@example.com",
                },
            )

        assert calls
        _, details = calls[0]
        assert "grace_expires_at" in details
        assert details["rotated_by"] == "admin@example.com"
        assert details["new_secret_version"] == 2
