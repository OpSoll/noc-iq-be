import json
import secrets
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, HttpUrl, field_validator
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.webhook import Webhook, WebhookDelivery, WebhookDeliveryStatus, WebhookEvent
from app.services.webhook_service import WEBHOOK_SCHEMA_VERSION

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


# --------------------------------------------------------------------------- #
# Schemas                                                                      #
# --------------------------------------------------------------------------- #

class WebhookCreate(BaseModel):
    name: str
    url: HttpUrl
    secret: Optional[str] = None
    events: List[WebhookEvent]
    max_retries: int = 3
    is_active: bool = True

    @field_validator("events")
    @classmethod
    def events_not_empty(cls, v: List[WebhookEvent]) -> List[WebhookEvent]:
        if not v:
            raise ValueError("At least one event must be specified.")
        return v


class WebhookUpdate(BaseModel):
    name: Optional[str] = None
    url: Optional[HttpUrl] = None
    secret: Optional[str] = None
    events: Optional[List[WebhookEvent]] = None
    max_retries: Optional[int] = None
    is_active: Optional[bool] = None


class WebhookResponse(BaseModel):
    id: UUID
    name: str
    url: str
    is_active: bool
    events: List[str]
    max_retries: int
    schema_version: str = WEBHOOK_SCHEMA_VERSION  # BE-082: explicit schema version

    model_config = {"from_attributes": True}


class WebhookDeliveryResponse(BaseModel):
    id: UUID
    webhook_id: UUID
    event: WebhookEvent
    status: WebhookDeliveryStatus
    attempt_count: int
    response_status_code: Optional[int]
    error_message: Optional[str]
    delivered_at: Optional[str]
    dead_lettered_at: Optional[str]  # BE-086: Include dead-letter timestamp
    created_at: str

    model_config = {"from_attributes": True}


class WebhookSecretRotateResponse(BaseModel):
    webhook_id: UUID
    new_secret: str
    message: str


class WebhookReplayRequest(BaseModel):
    device_id: Optional[str] = None
    outage_id: Optional[str] = None
    limit: int = 50


class WebhookReplayResponse(BaseModel):
    replayed_count: int
    message: str


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #

def _get_webhook_or_404(db: Session, webhook_id: UUID) -> Webhook:
    webhook = db.query(Webhook).filter(Webhook.id == webhook_id).first()
    if not webhook:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook not found.")
    return webhook


def _serialize_webhook(webhook: Webhook) -> WebhookResponse:
    try:
        events = json.loads(webhook.events)
    except (json.JSONDecodeError, TypeError):
        events = []
    return WebhookResponse(
        id=webhook.id,
        name=webhook.name,
        url=webhook.url,
        is_active=webhook.is_active,
        events=events,
        max_retries=webhook.max_retries,
    )


def _serialize_delivery(delivery: WebhookDelivery) -> WebhookDeliveryResponse:
    return WebhookDeliveryResponse(
        id=delivery.id,
        webhook_id=delivery.webhook_id,
        event=delivery.event,
        status=delivery.status,
        attempt_count=delivery.attempt_count,
        response_status_code=delivery.response_status_code,
        error_message=delivery.error_message,
        delivered_at=delivery.delivered_at.isoformat() if delivery.delivered_at else None,
        dead_lettered_at=delivery.dead_lettered_at.isoformat() if delivery.dead_lettered_at else None,
        created_at=delivery.created_at.isoformat(),
    )


# --------------------------------------------------------------------------- #
# Endpoints                                                                    #
# --------------------------------------------------------------------------- #

@router.post("", response_model=WebhookResponse, status_code=status.HTTP_201_CREATED)
def create_webhook(payload: WebhookCreate, db: Session = Depends(get_db)):
    webhook = Webhook(
        name=payload.name,
        url=str(payload.url),
        secret=payload.secret,
        events=json.dumps([e.value for e in payload.events]),
        max_retries=payload.max_retries,
        is_active=payload.is_active,
    )
    db.add(webhook)
    db.commit()
    db.refresh(webhook)
    return _serialize_webhook(webhook)


@router.get("", response_model=List[WebhookResponse])
def list_webhooks(
    is_active: Optional[bool] = Query(None),
    name: Optional[str] = Query(None, description="Filter by name (case-insensitive substring match)"),  # BE-083
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),  # BE-083
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),  # BE-083
    db: Session = Depends(get_db),
):
    query = db.query(Webhook)
    if is_active is not None:
        query = query.filter(Webhook.is_active == is_active)
    if name:
        query = query.filter(Webhook.name.ilike(f"%{name}%"))
    offset = (page - 1) * page_size
    return [_serialize_webhook(w) for w in query.offset(offset).limit(page_size).all()]


@router.get("/{webhook_id}", response_model=WebhookResponse)
def get_webhook(webhook_id: UUID, db: Session = Depends(get_db)):
    webhook = _get_webhook_or_404(db, webhook_id)
    return _serialize_webhook(webhook)


@router.patch("/{webhook_id}", response_model=WebhookResponse)
def update_webhook(webhook_id: UUID, payload: WebhookUpdate, db: Session = Depends(get_db)):
    webhook = _get_webhook_or_404(db, webhook_id)

    if payload.name is not None:
        webhook.name = payload.name
    if payload.url is not None:
        webhook.url = str(payload.url)
    if payload.secret is not None:
        webhook.secret = payload.secret
    if payload.events is not None:
        webhook.events = json.dumps([e.value for e in payload.events])
    if payload.max_retries is not None:
        webhook.max_retries = payload.max_retries
    if payload.is_active is not None:
        webhook.is_active = payload.is_active

    db.commit()
    db.refresh(webhook)
    return _serialize_webhook(webhook)


@router.delete("/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_webhook(webhook_id: UUID, db: Session = Depends(get_db)):
    webhook = _get_webhook_or_404(db, webhook_id)
    db.delete(webhook)
    db.commit()


@router.get("/{webhook_id}/deliveries", response_model=List[WebhookDeliveryResponse])
def list_webhook_deliveries(
    webhook_id: UUID,
    status_filter: Optional[WebhookDeliveryStatus] = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, description="Number of records to skip"),  # BE-083
    db: Session = Depends(get_db),
):
    _get_webhook_or_404(db, webhook_id)
    query = (
        db.query(WebhookDelivery)
        .filter(WebhookDelivery.webhook_id == webhook_id)
        .order_by(WebhookDelivery.created_at.desc())
    )
    if status_filter is not None:
        query = query.filter(WebhookDelivery.status == status_filter)
    return [_serialize_delivery(d) for d in query.offset(offset).limit(limit).all()]


@router.post("/{webhook_id}/rotate-secret", response_model=WebhookSecretRotateResponse)  # BE-084
def rotate_webhook_secret(webhook_id: UUID, db: Session = Depends(get_db)):
    """Rotate the webhook signing secret. The old secret is immediately invalidated;
    all subsequent deliveries will be signed with the new secret."""
    webhook = _get_webhook_or_404(db, webhook_id)
    new_secret = secrets.token_hex(32)
    webhook.secret = new_secret
    db.commit()
    return WebhookSecretRotateResponse(
        webhook_id=webhook.id,
        new_secret=new_secret,
        message="Secret rotated. Update your consumer to use the new secret immediately.",
    )


@router.post("/{webhook_id}/deliveries/{delivery_id}/retry", response_model=WebhookDeliveryResponse)
def retry_delivery(webhook_id: UUID, delivery_id: UUID, db: Session = Depends(get_db)):
    _get_webhook_or_404(db, webhook_id)
    delivery = (
        db.query(WebhookDelivery)
        .filter(
            WebhookDelivery.id == delivery_id,
            WebhookDelivery.webhook_id == webhook_id,
        )
        .first()
    )
    if not delivery:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Delivery not found.")
    if delivery.status == WebhookDeliveryStatus.SUCCESS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Delivery already succeeded; retry not needed.",
        )

    from app.services.webhook_service import dispatch_delivery
    dispatch_delivery(db, delivery.id)
    db.refresh(delivery)
    return _serialize_delivery(delivery)


# BE-086: Dead-letter handling endpoints

@router.get("/{webhook_id}/dead-letter-deliveries", response_model=List[WebhookDeliveryResponse])
def list_dead_letter_deliveries(
    webhook_id: UUID,
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """List dead-lettered deliveries for a webhook."""
    _get_webhook_or_404(db, webhook_id)
    from app.services.webhook_service import get_dead_letter_deliveries
    deliveries = get_dead_letter_deliveries(db, webhook_id=webhook_id, limit=limit)
    return [_serialize_delivery(d) for d in deliveries]


@router.post("/{webhook_id}/deliveries/{delivery_id}/replay", response_model=WebhookDeliveryResponse)
def replay_dead_letter_delivery(
    webhook_id: UUID, 
    delivery_id: UUID, 
    db: Session = Depends(get_db)
):
    """Replay a dead-lettered delivery."""
    _get_webhook_or_404(db, webhook_id)
    delivery = (
        db.query(WebhookDelivery)
        .filter(
            WebhookDelivery.id == delivery_id,
            WebhookDelivery.webhook_id == webhook_id,
        )
        .first()
    )
    if not delivery:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Delivery not found.")
    
    from app.services.webhook_service import replay_dead_letter_delivery
    success = replay_dead_letter_delivery(db, delivery_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to replay delivery. It may not be in dead-letter status."
        )
    
    db.refresh(delivery)
    return _serialize_delivery(delivery)


# BE-085: Webhook replay by event or outage filters

@router.post("/replay-by-context", response_model=WebhookReplayResponse)
def replay_deliveries_by_context(
    event: WebhookEvent,
    payload: WebhookReplayRequest,
    db: Session = Depends(get_db)
):
    """Replay deliveries by event and context (device or outage)."""
    from app.services.webhook_service import replay_deliveries_by_event_context
    
    replayed_count = replay_deliveries_by_event_context(
        db,
        event=event,
        device_id=payload.device_id,
        outage_id=payload.outage_id,
        limit=payload.limit
    )
    
    return WebhookReplayResponse(
        replayed_count=replayed_count,
        message=f"Replayed {replayed_count} deliveries for event {event.value}"
    )
