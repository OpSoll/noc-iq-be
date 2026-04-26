from datetime import datetime
from sqlalchemy import Column, DateTime, Integer, String
from app.db.base import Base

class UserORM(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(255), nullable=True)
    role = Column(String(50), default="engineer")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    # Auth rate limiting fields
    failed_login_attempts = Column(Integer, default=0)
    locked_until = Column(DateTime, nullable=True)
