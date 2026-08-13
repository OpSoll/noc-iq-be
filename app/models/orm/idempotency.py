from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text

from app.db.base import Base


class IdempotencyKeyORM(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String(255), nullable=False, unique=True, index=True)
    response_json = Column(Text, nullable=True)
    status_code = Column(Integer, nullable=True)
    created_at = Column(DateTime, nullable=True, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=True, index=True)
    endpoint = Column(String(255), nullable=True)
    processed_at = Column(DateTime, nullable=True, default=datetime.utcnow)
