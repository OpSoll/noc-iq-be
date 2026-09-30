"""ORM model for the payment dead-letter queue (issue #561)."""
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Float, Index, Integer, String, Text

from app.db.base import Base


class PaymentDeadLetterORM(Base):
    __tablename__ = "payment_dead_letter_queue"

    id = Column(String, primary_key=True, index=True)
    original_payment_id = Column(String, nullable=False, index=True)
    transaction_hash = Column(String(255), nullable=True)
    from_address = Column(String(255), nullable=False)
    to_address = Column(String(255), nullable=False)
    amount = Column(Float, nullable=False)
    asset_code = Column(String(20), nullable=False)
    error_code = Column(String(100), nullable=True)
    error_description = Column(Text, nullable=True)
    retry_class = Column(String(50), nullable=True)
    retry_count = Column(Integer, nullable=False, default=0)
    resubmitted = Column(Boolean, nullable=False, default=False)
    resubmitted_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.now(timezone.utc),
    )
    outage_id = Column(String, nullable=True)
    sla_result_id = Column(Integer, nullable=True)
    raw_horizon_response = Column(Text, nullable=True)

    __table_args__ = (
        Index("ix_payment_dlq_created_at", "created_at"),
    )
