from sqlalchemy import Column, Integer, String, DateTime
from datetime import datetime
from app.db.base_class import Base

class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String, unique=True, nullable=False)
    endpoint = Column(String, nullable=False)
    processed_at = Column(DateTime, default=datetime.utcnow)
