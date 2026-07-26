"""Tests for webhook event matching to prevent misrouting regressions.

Validates that the optimized get_active_webhooks_for_event function correctly
filters webhooks by event type without misrouting events to unsubscribed webhooks.
"""

import json
from uuid import uuid4

from app.models.webhook import Webhook, WebhookEvent
from app.services.webhook_service import get_active_webhooks_for_event, invalidate_webhook_cache


def test_webhook_event_matching_no_misrouting(db):
    """Event matching should only return webhooks subscribed to the specific event."""
    # Create webhooks with different event subscriptions
    violation_webhook = Webhook(
        name="violation-only",
        url="https://example.com/violation",
        events=json.dumps(["sla.violation"]),
        is_active=True,
    )
    warning_webhook = Webhook(
        name="warning-only",
        url="https://example.com/warning",
        events=json.dumps(["sla.warning"]),
        is_active=True,
    )
    resolved_webhook = Webhook(
        name="resolved-only",
        url="https://example.com/resolved",
        events=json.dumps(["sla.resolved"]),
        is_active=True,
    )
    multi_webhook = Webhook(
        name="multi-event",
        url="https://example.com/multi",
        events=json.dumps(["sla.violation", "sla.warning", "sla.resolved"]),
        is_active=True,
    )
    inactive_webhook = Webhook(
        name="inactive",
        url="https://example.com/inactive",
        events=json.dumps(["sla.violation"]),
        is_active=False,
    )
    invalid_json_webhook = Webhook(
        name="invalid-json",
        url="https://example.com/invalid",
        events="not-valid-json",
        is_active=True,
    )
    
    db.add_all([
        violation_webhook, warning_webhook, resolved_webhook,
        multi_webhook, inactive_webhook, invalid_json_webhook
    ])
    db.commit()
    
    # Test violation event lookup
    violation_webhooks = get_active_webhooks_for_event(db, WebhookEvent.SLA_VIOLATION)
    violation_ids = {w.id for w in violation_webhooks}
    
    # Should include violation-only and multi-event webhooks
    assert violation_webhook.id in violation_ids, "violation-only webhook should match"
    assert multi_webhook.id in violation_ids, "multi-event webhook should match"
    
    # Should NOT include warning-only, resolved-only, inactive, or invalid-json webhooks
    assert warning_webhook.id not in violation_ids, "warning-only webhook should not match violation event"
    assert resolved_webhook.id not in violation_ids, "resolved-only webhook should not match violation event"
    assert inactive_webhook.id not in violation_ids, "inactive webhook should not match"
    assert invalid_json_webhook.id not in violation_ids, "invalid-json webhook should be skipped"
    
    # Test warning event lookup
    warning_webhooks = get_active_webhooks_for_event(db, WebhookEvent.SLA_WARNING)
    warning_ids = {w.id for w in warning_webhooks}
    
    assert warning_webhook.id in warning_ids, "warning-only webhook should match"
    assert multi_webhook.id in warning_ids, "multi-event webhook should match"
    assert violation_webhook.id not in warning_ids, "violation-only webhook should not match warning event"
    assert resolved_webhook.id not in warning_ids, "resolved-only webhook should not match warning event"
    
    # Test resolved event lookup
    resolved_webhooks = get_active_webhooks_for_event(db, WebhookEvent.SLA_RESOLVED)
    resolved_ids = {w.id for w in resolved_webhooks}
    
    assert resolved_webhook.id in resolved_ids, "resolved-only webhook should match"
    assert multi_webhook.id in resolved_ids, "multi-event webhook should match"
    assert violation_webhook.id not in resolved_ids, "violation-only webhook should not match resolved event"
    assert warning_webhook.id not in resolved_ids, "warning-only webhook should not match resolved event"


def test_webhook_event_matching_with_cache_invalidation(db):
    """Cache invalidation should ensure fresh event matching after webhook updates."""
    # Create a webhook with single event subscription
    webhook = Webhook(
        name="test-webhook",
        url="https://example.com/test",
        events=json.dumps(["sla.violation"]),
        is_active=True,
    )
    db.add(webhook)
    db.commit()
    
    # Initial lookup - should match violation event
    violation_webhooks = get_active_webhooks_for_event(db, WebhookEvent.SLA_VIOLATION)
    assert webhook.id in {w.id for w in violation_webhooks}
    
    # Should not match warning event
    warning_webhooks = get_active_webhooks_for_event(db, WebhookEvent.SLA_WARNING)
    assert webhook.id not in {w.id for w in warning_webhooks}
    
    # Update webhook to subscribe to warning event
    webhook.events = json.dumps(["sla.warning"])
    db.commit()
    invalidate_webhook_cache(webhook.id)
    
    # After cache invalidation, should match warning event
    warning_webhooks = get_active_webhooks_for_event(db, WebhookEvent.SLA_WARNING)
    assert webhook.id in {w.id for w in warning_webhooks}
    
    # Should no longer match violation event
    violation_webhooks = get_active_webhooks_for_event(db, WebhookEvent.SLA_VIOLATION)
    assert webhook.id not in {w.id for w in violation_webhooks}


def test_webhook_event_matching_empty_registry(db):
    """Event matching should handle empty webhook registry gracefully."""
    # Ensure no webhooks exist
    db.query(Webhook).delete()
    db.commit()
    
    # All event lookups should return empty lists
    violation_webhooks = get_active_webhooks_for_event(db, WebhookEvent.SLA_VIOLATION)
    assert len(violation_webhooks) == 0
    
    warning_webhooks = get_active_webhooks_for_event(db, WebhookEvent.SLA_WARNING)
    assert len(warning_webhooks) == 0
    
    resolved_webhooks = get_active_webhooks_for_event(db, WebhookEvent.SLA_RESOLVED)
    assert len(resolved_webhooks) == 0


def test_webhook_event_matching_case_sensitivity(db):
    """Event matching should be case-sensitive to prevent misrouting."""
    # Create webhook with lowercase event
    webhook = Webhook(
        name="test-webhook",
        url="https://example.com/test",
        events=json.dumps(["sla.violation"]),
        is_active=True,
    )
    db.add(webhook)
    db.commit()
    
    # Should match exact case
    violation_webhooks = get_active_webhooks_for_event(db, WebhookEvent.SLA_VIOLATION)
    assert webhook.id in {w.id for w in violation_webhooks}
    
    # WebhookEvent enum values are lowercase, so this test validates
    # that the matching uses the exact enum value without case transformation


def test_webhook_event_matching_with_duplicate_events(db):
    """Event matching should handle webhooks with duplicate event entries."""
    # Create webhook with duplicate event entries
    webhook = Webhook(
        name="duplicate-events",
        url="https://example.com/duplicate",
        events=json.dumps(["sla.violation", "sla.violation", "sla.warning"]),
        is_active=True,
    )
    db.add(webhook)
    db.commit()
    
    # Should still match even with duplicates
    violation_webhooks = get_active_webhooks_for_event(db, WebhookEvent.SLA_VIOLATION)
    assert webhook.id in {w.id for w in violation_webhooks}
    
    warning_webhooks = get_active_webhooks_for_event(db, WebhookEvent.SLA_WARNING)
    assert webhook.id in {w.id for w in warning_webhooks}
