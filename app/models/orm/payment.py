from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Float, ForeignKey, Index, Integer, String, Text

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
    sla_result_id = Column(Integer, ForeignKey("sla_results.id", ondelete="SET NULL"), nullable=True, index=True, unique=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now(timezone.utc))
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    retry_count = Column(Integer, nullable=False, default=0)
    last_retried_at = Column(DateTime(timezone=True), nullable=True)
    failure_taxonomy = Column(String(50), nullable=True)
    idempotency_key = Column(String(255), nullable=True, unique=True, index=True)
    dead_letter_reason = Column(Text, nullable=True)
    dead_lettered_at = Column(DateTime(timezone=True), nullable=True)
    # Transaction timeout bounds (Stellar time_bounds)
    time_bounds_min = Column(Integer, nullable=False, default=0)
    time_bounds_max = Column(Integer, nullable=False, default=0)
    fee_re_estimation_pending = Column(Integer, nullable=False, default=0)  # Boolean stored as int for SQLite compat
    expired_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_payment_transactions_from_address", "from_address"),
        Index("ix_payment_transactions_to_address", "to_address"),
        # Issue #528: composite index for settlement lookups by wallet
        # address + status, ordered by newest first.
        Index("ix_payment_tx_to_addr_status", "to_address", "status", "created_at"),
    )
