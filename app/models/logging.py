from sqlalchemy import Column, Integer, String, JSON, DateTime
from datetime import datetime
from app.db.base_class import Base

class RequestLog(Base):
    __tablename__ = "request_logs"

    id = Column(Integer, primary_key=True, index=True)
    method = Column(String, index=True)
    path = Column(String, index=True)
    status_code = Column(Integer)
    payload = Column(JSON, nullable=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
