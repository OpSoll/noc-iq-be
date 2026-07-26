from sqlalchemy import Column, Integer, String, DateTime
from datetime import datetime
from app.db.base_class import Base

class OutageRecord(Base):
    __tablename__ = "outage_records"

    id = Column(Integer, primary_key=True, index=True)
    service_name = Column(String, index=True, nullable=False)
    description = Column(String, nullable=False)
    severity = Column(String, index=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
