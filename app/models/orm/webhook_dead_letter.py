"""ORM model for webhook dead-letter queue.

Stores webhook deliveries that have permanently failed after exhausting all
retry attempts. Records the final HTTP response status code and error
message for audit and debugging purposes.
"""
import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID

from app.db.base_class import Base


class WebhookDeadLetterORM(Base):
    """Dead-letter queue for webhook deliveries that failed all retries.

    After max retry exhaustion (default 5), failed webhook deliveries are
    routed here with their final HTTP response status code and error message.
    """
    __tablename__ = "webhook_dead_letter_queue"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    delivery_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    webhook_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    event = Column(String(100), nullable=False)
    payload = Column(Text, nullable=True)  # JSON-encoded payload

    # Final response metadata from last attempt
    response_status_code = Column(Integer, nullable=True)
    response_body = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)

    # Retry metadata
    attempt_count = Column(Integer, nullable=False, default=0)
    last_attempt_at = Column(DateTime(timezone=True), nullable=True)

    # Audit timestamps
    dead_lettered_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    # Redelivery tracking
    redelivered = Column(Integer, nullable=False, default=0)  # Boolean as int for SQLite
    redelivered_at = Column(DateTime(timezone=True), nullable=True)
