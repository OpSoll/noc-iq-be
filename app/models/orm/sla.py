from datetime import datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String

from app.db.base import Base


class SLAResultORM(Base):
    __tablename__ = "sla_results"

    id = Column(Integer, primary_key=True, autoincrement=True)
    outage_id = Column(String, ForeignKey("outages.id", ondelete="CASCADE"), nullable=False, index=True)
    status = Column(String(20), nullable=False)           # "met" | "violated"
    mttr_minutes = Column(Integer, nullable=False)
    threshold_minutes = Column(Integer, nullable=False)
    amount = Column(Float, nullable=False)
    payment_type = Column(String(20), nullable=False)     # "reward" | "penalty"
    rating = Column(String(20), nullable=False)           # "exceptional" | "excellent" | "good" | "poor"
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
