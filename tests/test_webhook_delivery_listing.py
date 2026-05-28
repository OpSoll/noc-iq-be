from datetime import datetime, timedelta

from app.models.webhook import Webhook, WebhookDelivery, WebhookDeliveryStatus, WebhookEvent


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
