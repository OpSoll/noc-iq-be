from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, String, Text

from app.db.base import Base


class OutageEventORM(Base):
    __tablename__ = "outage_events"

    id = Column(String, primary_key=True, index=True)
    outage_id = Column(String, ForeignKey("outages.id", ondelete="CASCADE"), nullable=False, index=True)
    event_type = Column(String(100), nullable=False)  # e.g. "created", "resolved", "sla_computed", "recomputed"
    detail = Column(Text, nullable=True)
    occurred_at = Column(DateTime, nullable=False, default=datetime.utcnow)
