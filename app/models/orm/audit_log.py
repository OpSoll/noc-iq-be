from datetime import datetime
from sqlalchemy import Column, DateTime, String, Integer, JSON
from app.db.base import Base

class AuditLogORM(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    event_type = Column(String(100), index=True, nullable=False)
    email = Column(String(255), index=True, nullable=True)
    details = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
