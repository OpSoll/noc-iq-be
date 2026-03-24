import uuid
from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import Column, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class DisputeStatus(str, PyEnum):
    PENDING = "pending"
    RESOLVED = "resolved"
    REJECTED = "rejected"


class SLADispute(Base):
    __tablename__ = "sla_disputes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sla_result_id = Column(Integer, ForeignKey("sla_results.id"), nullable=False, index=True)

    # Dispute metadata
    flagged_by = Column(String(255), nullable=False)
    dispute_reason = Column(Text, nullable=False)
    flagged_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Resolution metadata
    status = Column(Enum(DisputeStatus), default=DisputeStatus.PENDING, nullable=False)
    resolved_by = Column(String(255), nullable=True)
    resolution_notes = Column(Text, nullable=True)
    resolved_at = Column(DateTime, nullable=True)

    sla_result = relationship("SLAResultORM", back_populates="disputes")
