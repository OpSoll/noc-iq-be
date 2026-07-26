from datetime import datetime, timezone
from sqlalchemy import Column, DateTime, Boolean, String
from app.db.base import Base


class WalletORM(Base):
    __tablename__ = "wallets"

    id = Column(String, primary_key=True, index=True)
    user_id = Column(String(255), nullable=False, unique=True, index=True)
    public_key = Column(String(56), nullable=False, unique=True, index=True)
    funded = Column(Boolean, nullable=False, default=False)
    active = Column(Boolean, nullable=False, default=True)
    trustline_ready = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now(timezone.utc))
    last_updated = Column(DateTime(timezone=True), nullable=False, default=datetime.now(timezone.utc))
    cached_at = Column(DateTime(timezone=True), nullable=True)
