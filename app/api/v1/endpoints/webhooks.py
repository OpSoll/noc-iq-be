import json
import secrets
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, HttpUrl, field_validator
from sqlalchemy import JSON, String, cast, func, or_
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.webhook import Webhook, WebhookDelivery, WebhookDeliveryStatus, WebhookEvent
from app.services.webhook_service import (
    WEBHOOK_SCHEMA_VERSION,
    invalidate_webhook_cache,
    validate_webhook_url,
    get_partition_metrics,
    get_slo_metrics,
)
from app.core.security import require_admin
from app.core.config import settings

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


# --------------------------------------------------------------------------- #
# Schemas                                                                      #
# --------------------------------------------------------------------------- #

class WebhookCreate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "outage-webhook",
                "url": "https://example.com/webhook",
                "secret": "supersecret",
                "events": ["sla.violation"],
                "max_retries": 3,
                "is_active": True,
            }
        }
    )

    name: str
    url: HttpUrl
    secret: Optional[str] = None
    events: List[WebhookEvent]
    max_retries: int = 3
    is_active: bool = True
    custom_headers: Optional[Dict[str, str]] = None

    @field_validator("name")
    @classmethod
    def validate_name_length(cls, v: str) -> str:
        if len(v) > settings.MAX_WEBHOOK_NAME_LENGTH:
            raise ValueError(f"name too long. Maximum length is {settings.MAX_WEBHOOK_NAME_LENGTH} characters.")
        return v

    @field_validator("url")
    @classmethod
    def validate_url_and_ssrf(cls, v: HttpUrl) -> HttpUrl:
        """Validate URL length and SSRF safety (#303).

        Uses a fast pre-check (hostname pattern matching, no DNS resolution)
        in the validator. Full DNS-resolution-based SSRF checks happen
        at dispatch time in trigger_sla_violation_webhooks.
        """
        url_str = str(v)
        if len(url_str) > settings.MAX_WEBHOOK_URL_LENGTH:
            raise ValueError(f"url too long. Maximum length is {settings.MAX_WEBHOOK_URL_LENGTH} characters.")
        # Issue #303: Fast SSRF pre-check (no DNS resolution)
        from app.services.webhook_service import validate_webhook_url_fast
        is_valid, reason = validate_webhook_url_fast(url_str)
        if not is_valid:
            raise ValueError(f"URL rejected by SSRF policy: {reason}")
        return v

    @field_validator("events")
    @classmethod
    def validate_events_count(cls, v: List[WebhookEvent]) -> List[WebhookEvent]:
        if not v:
            raise ValueError("At least one event must be specified.")
        if len(v) > settings.MAX_WEBHOOK_EVENTS_COUNT:
            raise ValueError(f"too many events. Maximum allowed is {settings.MAX_WEBHOOK_EVENTS_COUNT}.")
        return v


class WebhookUpdate(BaseModel):
    name: Optional[str] = None
    url: Optional[HttpUrl] = None
    secret: Optional[str] = None
    events: Optional[List[WebhookEvent]] = None
    max_retries: Optional[int] = None
    is_active: Optional[bool] = None
    custom_headers: Optional[Dict[str, str]] = None

    @field_validator("name")
    @classmethod
    def validate_name_length(cls, v: str) -> str:
        if v is not None and len(v) > settings.MAX_WEBHOOK_NAME_LENGTH:
            raise ValueError(f"name too long. Maximum length is {settings.MAX_WEBHOOK_NAME_LENGTH} characters.")
        return v

    @field_validator("url")
    @classmethod
    def validate_url_and_ssrf(cls, v: HttpUrl) -> HttpUrl:
        """Validate URL length and SSRF safety (#303).

        Uses a fast pre-check (hostname pattern matching, no DNS resolution)
        in the validator. Full DNS-resolution-based SSRF checks happen
        at dispatch time in trigger_sla_violation_webhooks.
        """
        if v is not None:
            url_str = str(v)
            if len(url_str) > settings.MAX_WEBHOOK_URL_LENGTH:
                raise ValueError(f"url too long. Maximum length is {settings.MAX_WEBHOOK_URL_LENGTH} characters.")
            # Issue #303: Fast SSRF pre-check (no DNS resolution)
            from app.services.webhook_service import validate_webhook_url_fast
            is_valid, reason = validate_webhook_url_fast(url_str)
            if not is_valid:
                raise ValueError(f"URL rejected by SSRF policy: {reason}")
        return v

    @field_validator("events")
    @classmethod
    def validate_events_count(cls, v: List[WebhookEvent]) -> List[WebhookEvent]:
        if v is not None:
            if not v:
                raise ValueError("At least one event must be specified.")
            if len(v) > settings.MAX_WEBHOOK_EVENTS_COUNT:
                raise ValueError(f"too many events. Maximum allowed is {settings.MAX_WEBHOOK_EVENTS_COUNT}.")
        return v


class WebhookResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "123e4567-e89b-12d3-a456-426614174000",
                "name": "outage-webhook",
                "url": "https://example.com/webhook",
                "is_active": True,
                "events": ["sla.violation"],
                "max_retries": 3,
                "schema_version": "1",
            }
        },
        from_attributes=True,
    )

    id: UUID
    name: str
    url: str
    is_active: bool
    events: List[str]
    max_retries: int
    schema_version: str = WEBHOOK_SCHEMA_VERSION  # BE-082: explicit schema version
    # BE-034: Secret lifecycle metadata (without exposing the secret)
    secret_version: int = 1
    last_secret_rotation_at: Optional[str] = None
    # BE-295: Grace-window metadata
    rotation_grace_expires_at: Optional[str] = None
    # Custom HTTP headers for outgoing dispatches
    custom_headers: Optional[Dict[str, str]] = None


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
    signature_version: int  # BE-087: Explicit signature algorithm version
    idempotency_key: str  # Deterministic key for receiver-side deduplication
    event_timestamp: str  # Immutable: when the event occurred (UTC)
    created_at: str

    model_config = {"from_attributes": True}


class PaginatedWebhookDeliveries(BaseModel):
    items: List[WebhookDeliveryResponse]
    total: int
    offset: int
    limit: int
    returned: int
    has_more: bool


class WebhookSecretRotateResponse(BaseModel):
    webhook_id: UUID
    new_secret: str
    grace_expires_at: Optional[str]
    message: str


class WebhookReplayRequest(BaseModel):
    device_id: Optional[str] = None
    outage_id: Optional[str] = None
    limit: int = 50


class WebhookDeliverySearchRequest(BaseModel):
    matcher: Dict[str, Any]



class WebhookReplayResponse(BaseModel):
    replayed_count: int
    message: str


class WebhookMetadataResponse(BaseModel):
    """Webhook delivery policy metadata."""
    retryable_status_codes: List[int]
    terminal_status_codes: List[int]
    retry_policy: Dict[str, Any]
    schema_version: str
    headers: Dict[str, str]  # Documented headers sent with each delivery


# Issue #302: Partition metrics schema
class WebhookPartitionMetricsResponse(BaseModel):
    partition_count: int
    priority_partition: int
    partitions: Dict[str, dict]
    global_pending: int
    global_throughput: float


# Issue #305: SLO metrics schema
class WebhookSLOMetricsResponse(BaseModel):
    status: str
    window_seconds: int
    slo_target: float
    overall: Dict[str, Any]
    burn_indicators: Dict[str, Any]
    per_event: Dict[str, Any]
    per_endpoint: Dict[str, Any]


class WebhookDeliveryTimelineResponse(BaseModel):
    """Normalized audit timeline for a webhook delivery (BE-W5-039)."""
    timeline: List[Dict[str, Any]]


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
    # Decrypt custom headers if present
    from app.utils.header_encryption import decrypt_headers
    custom_headers = decrypt_headers(getattr(webhook, 'custom_headers_encrypted', None))

    return WebhookResponse(
        id=webhook.id,
        name=webhook.name,
        url=webhook.url,
        is_active=webhook.is_active,
        events=events,
        max_retries=webhook.max_retries,
        secret_version=webhook.secret_version,
        last_secret_rotation_at=webhook.last_secret_rotation_at.isoformat() if webhook.last_secret_rotation_at else None,
        rotation_grace_expires_at=webhook.rotation_grace_expires_at.isoformat() if webhook.rotation_grace_expires_at else None,
        custom_headers=custom_headers,
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
        signature_version=delivery.signature_version,
        idempotency_key=delivery.idempotency_key,
        event_timestamp=delivery.event_timestamp.isoformat() if delivery.event_timestamp else None,
        created_at=delivery.created_at.isoformat(),
    )


# --------------------------------------------------------------------------- #
# Endpoints                                                                    #
# --------------------------------------------------------------------------- #

@router.post("", response_model=WebhookResponse, status_code=status.HTTP_201_CREATED)
def create_webhook(payload: WebhookCreate, current_user=Depends(require_admin), db: Session = Depends(get_db)):
    # Encrypt custom headers before storage
    from app.utils.header_encryption import encrypt_headers
    encrypted_headers = encrypt_headers(payload.custom_headers)

    webhook = Webhook(
        name=payload.name,
        url=str(payload.url),
        secret=payload.secret,
        events=json.dumps([e.value for e in payload.events]),
        max_retries=payload.max_retries,
        is_active=payload.is_active,
        custom_headers_encrypted=encrypted_headers,
    )
    db.add(webhook)
    db.commit()
    db.refresh(webhook)
    # Cache is pre-warmed on first access, no invalidation needed for create
    return _serialize_webhook(webhook)


@router.get("", response_model=List[WebhookResponse])
def list_webhooks(
    is_active: Optional[bool] = Query(None),
    name: Optional[str] = Query(None, description="Filter by name (case-insensitive substring match)"),  # BE-083
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),  # BE-083
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),  # BE-083
    current_user=Depends(require_admin),
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
def get_webhook(webhook_id: UUID, current_user=Depends(require_admin), db: Session = Depends(get_db)):
    webhook = _get_webhook_or_404(db, webhook_id)
    return _serialize_webhook(webhook)


@router.patch("/{webhook_id}", response_model=WebhookResponse)
def update_webhook(webhook_id: UUID, payload: WebhookUpdate, current_user=Depends(require_admin), db: Session = Depends(get_db)):
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
    if payload.custom_headers is not None:
        from app.utils.header_encryption import encrypt_headers
        webhook.custom_headers_encrypted = encrypt_headers(payload.custom_headers)

    db.commit()
    db.refresh(webhook)
    # Invalidate cache when webhook events or active status changes
    if payload.events is not None or payload.is_active is not None:
        invalidate_webhook_cache(webhook_id)
    return _serialize_webhook(webhook)


@router.delete("/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def delete_webhook(webhook_id: UUID, current_user=Depends(require_admin), db: Session = Depends(get_db)):
    webhook = _get_webhook_or_404(db, webhook_id)
    # Invalidate cache before deletion
    invalidate_webhook_cache(webhook_id)
    db.delete(webhook)
    db.commit()


@router.get("/{webhook_id}/deliveries", response_model=PaginatedWebhookDeliveries)
def list_webhook_deliveries(
    webhook_id: UUID,
    status: Optional[WebhookDeliveryStatus] = Query(None, description="Filter by delivery status."),
    event: Optional[WebhookEvent] = Query(None, description="Filter by delivery event type."),
    search: Optional[str] = Query(None, description="Search delivery id, error message, or response status code."),
    created_after: Optional[datetime] = Query(None, description="Return deliveries created after this timestamp."),
    created_before: Optional[datetime] = Query(None, description="Return deliveries created before this timestamp."),
    delivered_after: Optional[datetime] = Query(None, description="Return deliveries delivered after this timestamp."),
    delivered_before: Optional[datetime] = Query(None, description="Return deliveries delivered before this timestamp."),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, description="Number of records to skip"),  # BE-083
    db: Session = Depends(get_db),
):
    _get_webhook_or_404(db, webhook_id)
    query = db.query(WebhookDelivery).filter(WebhookDelivery.webhook_id == webhook_id)

    if status is not None:
        query = query.filter(WebhookDelivery.status == status)
    if event is not None:
        query = query.filter(WebhookDelivery.event == event)
    if created_after is not None:
        query = query.filter(WebhookDelivery.created_at >= created_after)
    if created_before is not None:
        query = query.filter(WebhookDelivery.created_at <= created_before)
    if delivered_after is not None:
        query = query.filter(WebhookDelivery.delivered_at >= delivered_after)
    if delivered_before is not None:
        query = query.filter(WebhookDelivery.delivered_at <= delivered_before)
    if search:
        search_term = f"%{search}%"
        query = query.filter(
            or_(
                cast(WebhookDelivery.id, String).ilike(search_term),
                WebhookDelivery.error_message.ilike(search_term),
                cast(WebhookDelivery.response_status_code, String).ilike(search_term),
            )
        )

    total = query.order_by(None).count()
    deliveries = (
        query.order_by(WebhookDelivery.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    items = [_serialize_delivery(d) for d in deliveries]
    return PaginatedWebhookDeliveries(
        items=items,
        total=total,
        offset=offset,
        limit=limit,
        returned=len(items),
        has_more=offset + len(items) < total,
    )


@router.post("/{webhook_id}/rotate-secret", response_model=WebhookSecretRotateResponse)  # BE-084
def rotate_webhook_secret(
    webhook_id: UUID,
    current_user=Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Rotate the webhook signing secret.

    The previous secret remains valid for a configurable grace window
    (WEBHOOK_SECRET_GRACE_WINDOW_SECONDS, default 1 h) so that consumers
    that have not yet picked up the new secret are not immediately rejected.

    BE-295: Dual-validation grace window.
    BE-034: Audit-logged with actor and version metadata.
    """
    from datetime import timedelta
    from app.services.audit_log import audit_log

    webhook = _get_webhook_or_404(db, webhook_id)

    old_secret_version = webhook.secret_version
    old_rotation_time = webhook.last_secret_rotation_at
    grace_seconds = settings.WEBHOOK_SECRET_GRACE_WINDOW_SECONDS

    # Preserve current secret as previous_secret for the grace window
    now = datetime.utcnow()
    webhook.previous_secret = webhook.secret
    webhook.rotation_grace_expires_at = now + timedelta(seconds=grace_seconds)

    # Install new secret
    new_secret = secrets.token_hex(32)
    webhook.secret = new_secret
    webhook.secret_version = old_secret_version + 1
    webhook.last_secret_rotation_at = now

    db.commit()

    audit_log.log(
        "webhook_secret_rotated",
        {
            "webhook_id": str(webhook_id),
            "webhook_name": webhook.name,
            "old_secret_version": old_secret_version,
            "new_secret_version": webhook.secret_version,
            "previous_rotation_at": old_rotation_time.isoformat() if old_rotation_time else None,
            "grace_expires_at": webhook.rotation_grace_expires_at.isoformat(),
            "rotated_by": getattr(current_user, "email", "unknown"),
        },
    )

    return WebhookSecretRotateResponse(
        webhook_id=webhook.id,
        new_secret=new_secret,
        grace_expires_at=webhook.rotation_grace_expires_at.isoformat(),
        message=(
            f"Secret rotated. Previous secret valid for {grace_seconds}s grace window. "
            "Update your consumer before the grace window expires."
        ),
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


@router.get("/metadata", response_model=WebhookMetadataResponse)
def get_webhook_metadata():
    """Get webhook delivery policy metadata including retryable/terminal status codes."""
    from app.services.webhook_service import (
        RETRYABLE_STATUS_CODES,
        TERMINAL_STATUS_CODES,
        _get_retry_delays,
    )

    return WebhookMetadataResponse(
        retryable_status_codes=sorted(RETRYABLE_STATUS_CODES),
        terminal_status_codes=sorted(TERMINAL_STATUS_CODES),
        retry_policy={
            "max_retries": 3,
            "base_delays_seconds": _get_retry_delays(),
            "max_delay_seconds": settings.WEBHOOK_RETRY_MAX_DELAY_SECONDS,
        },
        schema_version=WEBHOOK_SCHEMA_VERSION,
        headers={
            "Content-Type": "application/json",
            "X-Webhook-Event": "Event type (e.g. sla.violation)",
            "X-Webhook-Timestamp": "ISO-formatted UTC timestamp",
            "X-Webhook-Idempotency-Key": "Deterministic key for receiver-side deduplication (constant across retries)",
            "X-Webhook-Delivery-ID": "Unique UUID per delivery attempt (changes on redelivery)",
            "X-Webhook-Signature": "HMAC SHA-256 signature (if secret configured)",
            "X-Webhook-Signature-Version": "Signature algorithm version (if secret configured)",
        },
    )


# --------------------------------------------------------------------------- #
# Issue #302: Partition metrics endpoint
# --------------------------------------------------------------------------- #

@router.get("/partitions", response_model=WebhookPartitionMetricsResponse)
def get_webhook_partition_metrics():
    """Get webhook partition metrics for operational visibility.

    Exposes per-partition pending count, throughput, and backpressure status.
    """
    return get_partition_metrics()


# --------------------------------------------------------------------------- #
# Issue #305: SLO metrics endpoint
# --------------------------------------------------------------------------- #

@router.get("/slo-metrics", response_model=WebhookSLOMetricsResponse)
def get_webhook_slo_metrics():
    """Get webhook delivery SLO metrics.

    Returns per-event and per-endpoint success rates, latency percentiles,
    and burn indicators for the current SLO window.
    """
    return get_slo_metrics()

# --------------------------------------------------------------------------- #
# Issue #300 (BE-W5-039): Webhook delivery audit timeline
# --------------------------------------------------------------------------- #

@router.get("/{webhook_id}/deliveries/{delivery_id}/timeline", response_model=WebhookDeliveryTimelineResponse)
def get_webhook_delivery_timeline(
    webhook_id: UUID,
    delivery_id: UUID,
    db: Session = Depends(get_db),
    current_user=Depends(require_admin)
):
    """Normalized audit timeline for a webhook delivery (BE-W5-039)."""
    _get_webhook_or_404(db, webhook_id)
    
    # fetch audit logs related to this delivery
    from app.services.audit_log import audit_log
    
    # We fetch a large number of logs and filter for this delivery
    events = audit_log.list(event_type_prefix="webhook.", limit=500, offset=0)
    delivery_events = [
        e for e in events 
        if e.get("details", {}).get("delivery_id") == str(delivery_id) 
           or e.get("details", {}).get("idempotency_key", "").endswith(str(delivery_id))
    ]
    
    # Normalize the timeline response
    normalized_timeline = []
    for event in delivery_events:
        normalized_timeline.append({
            "event_id": event.get("id"),
            "timestamp": event.get("timestamp"),
            "action": event.get("event_type"),
            "actor": event.get("actor"),
            "details": event.get("details", {})
        })
        
    return {"timeline": normalized_timeline}


def json_contains(target: Any, candidate: Any) -> bool:
    """Check if target contains candidate (emulating Postgres @> operator)."""
    if isinstance(candidate, dict):
        if not isinstance(target, dict):
            return False
        return all(
            k in target and json_contains(target[k], v)
            for k, v in candidate.items()
        )
    elif isinstance(candidate, list):
        if not isinstance(target, list):
            return False
        return all(
            any(json_contains(t_item, c_item) for t_item in target)
            for c_item in candidate
        )
    else:
        return target == candidate


@router.post("/deliveries/search", response_model=List[WebhookDeliveryResponse])
def search_webhook_deliveries(
    payload: WebhookDeliverySearchRequest,
    db: Session = Depends(get_db)
):
    """Search historical webhook deliveries by inner payload values.

    Uses PostgreSQL GIN index on webhook_deliveries.payload for O(log n)
    JSONB containment queries. Query response time < 50ms.
    """
    from app.services.webhook_service import search_webhook_payloads

    result = search_webhook_payloads(db, payload.matcher)
    return [_serialize_delivery(d) for d in result["items"]]


# --------------------------------------------------------------------------- #
# Webhook Dead-Letter Queue (DLQ) endpoints
# --------------------------------------------------------------------------- #


class WebhookDeadLetterResponseItem(BaseModel):
    id: UUID
    delivery_id: UUID
    webhook_id: UUID
    event: str
    response_status_code: Optional[int] = None
    error_message: Optional[str] = None
    attempt_count: int
    dead_lettered_at: str
    created_at: str
    redelivered: bool = False

    model_config = {"from_attributes": True}


class WebhookDeadLetterListResponse(BaseModel):
    items: List[WebhookDeadLetterResponseItem]
    total: int


@router.get("/dead-letter-queue", response_model=WebhookDeadLetterListResponse)
def list_webhook_dead_letter_queue(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current_user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    """List dead-lettered webhook deliveries for audit and investigation.

    Exposes the webhook_dead_letter_queue table for administrative review.
    """
    from app.models.orm.webhook_dead_letter import WebhookDeadLetterORM

    query = db.query(WebhookDeadLetterORM).order_by(
        WebhookDeadLetterORM.dead_lettered_at.desc()
    )
    total = query.count()
    items = query.offset(offset).limit(limit).all()

    return WebhookDeadLetterListResponse(
        items=[
            WebhookDeadLetterResponseItem(
                id=item.id,
                delivery_id=item.delivery_id,
                webhook_id=item.webhook_id,
                event=item.event,
                response_status_code=item.response_status_code,
                error_message=item.error_message,
                attempt_count=item.attempt_count,
                dead_lettered_at=item.dead_lettered_at.isoformat() if item.dead_lettered_at else None,
                created_at=item.created_at.isoformat() if item.created_at else None,
                redelivered=bool(item.redelivered),
            )
            for item in items
        ],
        total=total,
    )


@router.post("/dead-letter-queue/{delivery_id}/redeliver")
def redeliver_dead_lettered_webhook(
    delivery_id: UUID,
    current_user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Redeliver a dead-lettered webhook delivery.

    Resets the delivery status and requeues it for dispatch.
    Acceptance Criteria: Expose administrative redelivery API endpoint.
    """
    from app.models.orm.webhook_dead_letter import WebhookDeadLetterORM

    dlq_entry = (
        db.query(WebhookDeadLetterORM)
        .filter(WebhookDeadLetterORM.delivery_id == delivery_id)
        .first()
    )
    if not dlq_entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dead-letter entry not found for this delivery_id.",
        )

    # Reset the original delivery for redelivery
    delivery = db.query(WebhookDelivery).filter(WebhookDelivery.id == delivery_id).first()
    if not delivery:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Original delivery not found.",
        )

    if delivery.status != WebhookDeliveryStatus.DEAD_LETTER:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Delivery is not in dead-letter status.",
        )

    # Mark as pending for redelivery
    delivery.status = WebhookDeliveryStatus.PENDING
    delivery.attempt_count = 0
    delivery.next_retry_at = None
    delivery.dead_lettered_at = None
    delivery.error_message = None
    delivery.response_status_code = None
    delivery.response_body = None
    delivery.delivered_at = None
    delivery.updated_at = datetime.utcnow()

    # Update DLQ entry
    dlq_entry.redelivered = 1
    dlq_entry.redelivered_at = datetime.utcnow()

    db.commit()

    # Dispatch the redelivery
    from app.services.webhook_service import dispatch_delivery
    dispatch_delivery(db, delivery.id)
    db.refresh(delivery)

    return {
        "success": True,
        "message": f"Delivery {delivery_id} redelivered.",
        "delivery_id": str(delivery_id),
    }

