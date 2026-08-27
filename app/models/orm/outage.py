from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Float, Index, Integer, JSON, String, Text
from sqlalchemy.dialects.postgresql import ARRAY as PG_ARRAY

from app.db.base import Base


class OutageORM(Base):
    __tablename__ = "outages"

    # Issue #515: composite index backing queries that filter open outages by
    # site ID (see migration 0025_add_outages_site_status_idx).
    __table_args__ = (
        Index("ix_outages_site_status_detected", "site_id", "status", "detected_at"),
    )

    id = Column(String, primary_key=True, index=True)
    site_name = Column(String(255), nullable=False)
    site_id = Column(String(255), nullable=True)
    severity = Column(String(50), nullable=False)
    status = Column(String(50), nullable=False, default="open", index=True)
    detected_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now(timezone.utc))
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    description = Column(Text, nullable=False)
    affected_services = Column(JSON().with_variant(PG_ARRAY(String), "postgresql"), nullable=False, default=list)
    affected_subscribers = Column(Integer, nullable=True)
    assigned_to = Column(String(255), nullable=True)
    created_by = Column(String(255), nullable=True)
    location = Column(JSON, nullable=True)          # {"latitude": float, "longitude": float}
    sla_status = Column(JSON, nullable=True)        # SLAStatus dict
    mttr_minutes = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.now(timezone.utc),
        onupdate=datetime.now(timezone.utc),
    )
