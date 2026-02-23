import json
import hmac
import hashlib
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from uuid import UUID

import httpx
from sqlalchemy.orm import Session

from app.models.webhook import Webhook, WebhookDelivery, WebhookDeliveryStatus, WebhookEvent

logger = logging.getLogger(__name__)

RETRY_DELAYS = [30, 120, 600]  # seconds: 30s, 2m, 10m (exponential backoff)


def _sign_payload(secret: str, payload: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def _build_headers(webhook: Webhook, payload: str) -> Dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "X-Webhook-Event": WebhookEvent.SLA_VIOLATION,
        "X-Webhook-Timestamp": datetime.utcnow().isoformat(),
    }
    if webhook.secret:
        headers["X-Webhook-Signature"] = f"sha256={_sign_payload(webhook.secret, payload)}"
    return headers


def get_active_webhooks_for_event(db: Session, event: WebhookEvent) -> List[Webhook]:
    webhooks = db.query(Webhook).filter(Webhook.is_active == True).all()
    result = []
    for webhook in webhooks:
        try:
            events = json.loads(webhook.events)
            if event.value in events:
                result.append(webhook)
        except (json.JSONDecodeError, TypeError):
            logger.warning("Webhook %s has invalid events JSON, skipping.", webhook.id)
    return result


def create_delivery(
    db: Session,
    webhook: Webhook,
    event: WebhookEvent,
    payload: Dict[str, Any],
) -> WebhookDelivery:
    delivery = WebhookDelivery(
        webhook_id=webhook.id,
        event=event,
        payload=json.dumps(payload),
        status=WebhookDeliveryStatus.PENDING,
    )
    db.add(delivery)
    db.commit()
    db.refresh(delivery)
    return delivery


def _attempt_delivery(delivery: WebhookDelivery, webhook: Webhook) -> bool:
    payload_str = delivery.payload
    headers = _build_headers(webhook, payload_str)

    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.post(webhook.url, content=payload_str, headers=headers)
        delivery.response_status_code = response.status_code
        delivery.response_body = response.text[:4000]

        if response.is_success:
            return True
        else:
            delivery.error_message = f"Non-success status: {response.status_code}"
            return False

    except httpx.TimeoutException as exc:
        delivery.error_message = f"Request timed out: {exc}"
        logger.warning("Webhook delivery %s timed out.", delivery.id)
        return False
    except httpx.RequestError as exc:
        delivery.error_message = f"Request error: {exc}"
        logger.warning("Webhook delivery %s failed with request error: %s", delivery.id, exc)
        return False


def dispatch_delivery(db: Session, delivery_id: UUID) -> None:
    delivery = db.query(WebhookDelivery).filter(WebhookDelivery.id == delivery_id).first()
    if not delivery:
        logger.error("WebhookDelivery %s not found.", delivery_id)
        return

    webhook = delivery.webhook
    delivery.attempt_count += 1
    delivery.status = WebhookDeliveryStatus.RETRYING if delivery.attempt_count > 1 else WebhookDeliveryStatus.PENDING
    delivery.updated_at = datetime.utcnow()
    db.commit()

    success = _attempt_delivery(delivery, webhook)

    if success:
        delivery.status = WebhookDeliveryStatus.SUCCESS
        delivery.delivered_at = datetime.utcnow()
        delivery.next_retry_at = None
        logger.info(
            "Webhook delivery %s succeeded on attempt %d for webhook %s.",
            delivery.id, delivery.attempt_count, webhook.id,
        )
    else:
        retry_index = delivery.attempt_count - 1
        max_retries = webhook.max_retries or 3

        if retry_index < max_retries and retry_index < len(RETRY_DELAYS):
            delay = RETRY_DELAYS[retry_index] * (2 ** retry_index)
            delivery.next_retry_at = datetime.utcnow() + timedelta(seconds=delay)
            delivery.status = WebhookDeliveryStatus.RETRYING
            logger.warning(
                "Webhook delivery %s failed (attempt %d). Retrying in %ds.",
                delivery.id, delivery.attempt_count, delay,
            )
        else:
            delivery.status = WebhookDeliveryStatus.FAILED
            delivery.next_retry_at = None
            logger.error(
                "Webhook delivery %s permanently failed after %d attempts.",
                delivery.id, delivery.attempt_count,
            )

    delivery.updated_at = datetime.utcnow()
    db.commit()


def trigger_sla_violation_webhooks(
    db: Session,
    sla_data: Dict[str, Any],
    event: WebhookEvent = WebhookEvent.SLA_VIOLATION,
) -> List[WebhookDelivery]:
    webhooks = get_active_webhooks_for_event(db, event)
    deliveries = []

    payload = {
        "event": event.value,
        "timestamp": datetime.utcnow().isoformat(),
        "data": sla_data,
    }

    for webhook in webhooks:
        delivery = create_delivery(db, webhook, event, payload)
        deliveries.append(delivery)
        logger.info(
            "Queued webhook delivery %s for webhook %s on event %s.",
            delivery.id, webhook.id, event.value,
        )
        # Dispatch immediately (in production, offload to a background task/queue)
        dispatch_delivery(db, delivery.id)

    return deliveries


def retry_pending_deliveries(db: Session) -> int:
    now = datetime.utcnow()
    due_deliveries = (
        db.query(WebhookDelivery)
        .filter(
            WebhookDelivery.status == WebhookDeliveryStatus.RETRYING,
            WebhookDelivery.next_retry_at <= now,
        )
        .all()
    )

    count = 0
    for delivery in due_deliveries:
        dispatch_delivery(db, delivery.id)
        count += 1

    return count
