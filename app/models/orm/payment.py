from datetime import datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, String

from app.db.base import Base


class PaymentTransactionORM(Base):
    __tablename__ = "payment_transactions"

    id = Column(String, primary_key=True, index=True)
    transaction_hash = Column(String(255), nullable=False, unique=True)
    type = Column(String(50), nullable=False)
    amount = Column(Float, nullable=False)
    asset_code = Column(String(20), nullable=False)
    from_address = Column(String(255), nullable=False)
    to_address = Column(String(255), nullable=False)
    status = Column(String(50), nullable=False, default="pending", index=True)
    outage_id = Column(String, ForeignKey("outages.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    confirmed_at = Column(DateTime, nullable=True)
