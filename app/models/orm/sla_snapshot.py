from datetime import datetime

from sqlalchemy import Column, DateTime, Float, Integer, String

from app.db.base import Base


class SLAAnalyticsSnapshotORM(Base):
    __tablename__ = "sla_analytics_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_key = Column(String(100), nullable=False, index=True)  # e.g. "global" or "severity:critical"
    total_outages = Column(Integer, nullable=False, default=0)
    total_violations = Column(Integer, nullable=False, default=0)
    total_rewards = Column(Float, nullable=False, default=0.0)
    total_penalties = Column(Float, nullable=False, default=0.0)
    net_payout = Column(Float, nullable=False, default=0.0)
    avg_mttr = Column(Float, nullable=False, default=0.0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
