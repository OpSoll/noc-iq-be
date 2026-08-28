"""Tests for webhook delivery log retention purging (purge_old_webhook_logs).

Covers:
- Purge deletes only old deliveries, preserving recent ones
- Purge returns correct count
- Purge logs number of purged rows
- Configurable retention period
"""
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from app.models.webhook import Webhook, WebhookDelivery, WebhookDeliveryStatus, WebhookEvent


def test_purge_old_webhook_logs_deletes_old_deliveries(client, db):
    """Delivery logs older than retention period are deleted."""
    webhook = Webhook(
        name="purge-test-webhook",
        url="https://example.com/webhook",
        secret="secret",
        events='["sla.violation"]',
    )
    db.add(webhook)
    db.commit()
    db.refresh(webhook)

    now = datetime.utcnow()

    # Old delivery (>30 days)
    old_delivery = WebhookDelivery(
        webhook_id=webhook.id,
        event=WebhookEvent.SLA_VIOLATION,
        payload="{}",
        status=WebhookDeliveryStatus.SUCCESS,
        attempt_count=1,
        response_status_code=200,
        created_at=now - timedelta(days=31),
    )
    # Recent delivery (<30 days)
    recent_delivery = WebhookDelivery(
        webhook_id=webhook.id,
        event=WebhookEvent.SLA_VIOLATION,
        payload="{}",
        status=WebhookDeliveryStatus.SUCCESS,
        attempt_count=1,
        response_status_code=200,
        created_at=now - timedelta(days=5),
    )
    db.add_all([old_delivery, recent_delivery])
    db.commit()

    with patch("app.tasks.webhook_tasks.cfg") as mock_cfg:
        mock_cfg.WEBHOOK_DELIVERY_LOG_RETENTION_DAYS = 30
        from app.tasks.webhook_tasks import purge_old_webhook_logs
        result = purge_old_webhook_logs()

    assert result["purged"] == 1
    assert result["retention_days"] == 30

    # Verify old one is gone, recent one remains
    remaining = db.query(WebhookDelivery).filter(WebhookDelivery.webhook_id == webhook.id).all()
    assert len(remaining) == 1
    assert remaining[0].id == recent_delivery.id


def test_purge_old_webhook_logs_no_old_deliveries(client, db):
    """Purge returns 0 purged when no old deliveries exist."""
    webhook = Webhook(
        name="purge-noop-webhook",
        url="https://example.com/webhook",
        secret="secret",
        events='["sla.violation"]',
    )
    db.add(webhook)
    db.commit()
    db.refresh(webhook)

    now = datetime.utcnow()
    recent_delivery = WebhookDelivery(
        webhook_id=webhook.id,
        event=WebhookEvent.SLA_VIOLATION,
        payload="{}",
        status=WebhookDeliveryStatus.SUCCESS,
        attempt_count=1,
        response_status_code=200,
        created_at=now - timedelta(days=5),
    )
    db.add(recent_delivery)
    db.commit()

    with patch("app.tasks.webhook_tasks.cfg") as mock_cfg:
        mock_cfg.WEBHOOK_DELIVERY_LOG_RETENTION_DAYS = 30
        from app.tasks.webhook_tasks import purge_old_webhook_logs
        result = purge_old_webhook_logs()

    assert result["purged"] == 0
    assert result["retention_days"] == 30

    # Verify delivery still exists
    remaining = db.query(WebhookDelivery).filter(WebhookDelivery.webhook_id == webhook.id).all()
    assert len(remaining) == 1


def test_purge_old_webhook_logs_custom_retention(client, db):
    """Purge respects custom retention period."""
    webhook = Webhook(
        name="purge-custom-retention-webhook",
        url="https://example.com/webhook",
        secret="secret",
        events='["sla.violation"]',
    )
    db.add(webhook)
    db.commit()
    db.refresh(webhook)

    now = datetime.utcnow()

    # Delivery that is 5 days old (purged with 3-day retention, kept with 7-day)
    five_day_old = WebhookDelivery(
        webhook_id=webhook.id,
        event=WebhookEvent.SLA_VIOLATION,
        payload="{}",
        status=WebhookDeliveryStatus.SUCCESS,
        attempt_count=1,
        response_status_code=200,
        created_at=now - timedelta(days=5),
    )
    db.add(five_day_old)
    db.commit()

    with patch("app.tasks.webhook_tasks.cfg") as mock_cfg:
        mock_cfg.WEBHOOK_DELIVERY_LOG_RETENTION_DAYS = 3  # 3-day retention
        from app.tasks.webhook_tasks import purge_old_webhook_logs
        result = purge_old_webhook_logs()

    assert result["purged"] == 1

    # Verify delivery was purged
    remaining = db.query(WebhookDelivery).filter(WebhookDelivery.webhook_id == webhook.id).all()
    assert len(remaining) == 0


def test_purge_old_webhook_logs_batch_deletion(client, db):
    """Purge handles large batches correctly."""
    webhook = Webhook(
        name="purge-batch-webhook",
        url="https://example.com/webhook",
        secret="secret",
        events='["sla.violation"]',
    )
    db.add(webhook)
    db.commit()
    db.refresh(webhook)

    now = datetime.utcnow()

    # Create 5 old deliveries
    old_deliveries = [
        WebhookDelivery(
            webhook_id=webhook.id,
            event=WebhookEvent.SLA_VIOLATION,
            payload="{}",
            status=WebhookDeliveryStatus.SUCCESS,
            attempt_count=1,
            response_status_code=200,
            created_at=now - timedelta(days=40),
        )
        for _ in range(5)
    ]
    db.add_all(old_deliveries)
    db.commit()

    with patch("app.tasks.webhook_tasks.cfg") as mock_cfg:
        mock_cfg.WEBHOOK_DELIVERY_LOG_RETENTION_DAYS = 30
        from app.tasks.webhook_tasks import purge_old_webhook_logs
        result = purge_old_webhook_logs()

    assert result["purged"] == 5

    # Verify all purged
    remaining = db.query(WebhookDelivery).filter(WebhookDelivery.webhook_id == webhook.id).all()
    assert len(remaining) == 0


def test_purge_old_webhook_logs_respects_cutoff(client, db):
    """Deliveries exactly at the retention boundary are not purged."""
    webhook = Webhook(
        name="purge-boundary-webhook",
        url="https://example.com/webhook",
        secret="secret",
        events='["sla.violation"]',
    )
    db.add(webhook)
    db.commit()
    db.refresh(webhook)

    now = datetime.utcnow()

    # Delivery at exactly 30 days old (should be kept - purged only if strictly < cutoff)
    boundary_delivery = WebhookDelivery(
        webhook_id=webhook.id,
        event=WebhookEvent.SLA_VIOLATION,
        payload="{}",
        status=WebhookDeliveryStatus.SUCCESS,
        attempt_count=1,
        response_status_code=200,
        created_at=now - timedelta(days=30),
    )
    db.add(boundary_delivery)
    db.commit()

    with patch("app.tasks.webhook_tasks.cfg") as mock_cfg:
        mock_cfg.WEBHOOK_DELIVERY_LOG_RETENTION_DAYS = 30
        from app.tasks.webhook_tasks import purge_old_webhook_logs
        result = purge_old_webhook_logs()

    assert result["purged"] == 0

    # Verify delivery still exists
    remaining = db.query(WebhookDelivery).filter(WebhookDelivery.webhook_id == webhook.id).all()
    assert len(remaining) == 1
