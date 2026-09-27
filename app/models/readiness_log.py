from sqlalchemy import Column, Integer, String, JSON, DateTime
from datetime import datetime
from app.db.base_class import Base

class ReadinessLog(Base):
    __tablename__ = "readiness_logs"

    id = Column(Integer, primary_key=True, index=True)
    status = Column(String, index=True)
    dependencies_snapshot = Column(JSON)
    timestamp = Column(DateTime, default=datetime.utcnow)
