from sqlalchemy import Column, Integer, String, DateTime
from datetime import datetime
from app.db.base_class import Base

class SlaDispute(Base):
    __tablename__ = "sla_disputes"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, index=True)
    sla_id = Column(Integer, index=True, nullable=False)
    reason = Column(String, nullable=False)
    evidence_url = Column(String, nullable=True)
    state = Column(String, default="OPEN", index=True)
    resolution_notes = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
