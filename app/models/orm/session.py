from datetime import datetime
from sqlalchemy import Column, DateTime, String, ForeignKey
from app.db.base import Base

class SessionORM(Base):
    __tablename__ = "sessions"

    access_token = Column(String(255), primary_key=True, index=True)
    refresh_token = Column(String(255), unique=True, index=True, nullable=False)
    email = Column(String(255), ForeignKey("users.email"), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
