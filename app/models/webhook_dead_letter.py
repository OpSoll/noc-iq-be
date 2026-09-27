"""Pydantic model for webhook dead-letter queue entries.

Represents a webhook delivery that has permanently failed after exhausting
all retry attempts. Includes final HTTP response status and error details
for audit and redelivery purposes.
"""
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class WebhookDeadLetterEntry(BaseModel):
    """Dead-letter queue entry for a failed webhook delivery."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    delivery_id: UUID
    webhook_id: UUID
    event: str
    payload: Optional[str] = None

    # Final response metadata
    response_status_code: Optional[int] = None
    response_body: Optional[str] = None
    error_message: Optional[str] = None

    # Retry metadata
    attempt_count: int = 0
    last_attempt_at: Optional[datetime] = None

    # Audit timestamps
    dead_lettered_at: datetime
    created_at: datetime

    # Redelivery tracking
    redelivered: bool = False
    redelivered_at: Optional[datetime] = None


class WebhookDeadLetterResponse(BaseModel):
    """Response model for webhook dead-letter queue."""
    items: List[WebhookDeadLetterEntry]
    total: int
    offset: int = 0
    limit: int = 50
    has_more: bool = False


class WebhookRedeliveryRequest(BaseModel):
    """Request to redeliver a dead-lettered webhook."""
    delivery_id: UUID


class WebhookRedeliveryResponse(BaseModel):
    """Response for webhook redelivery."""
    success: bool
    message: str
    delivery_id: UUID
