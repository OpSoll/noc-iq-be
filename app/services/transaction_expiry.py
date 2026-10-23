"""Transaction timeout bounds and expiry management.

Enforces a maximum 300-second (5 minute) time window for unsubmitted payment
transactions. Transactions that exceed their time bounds are automatically
expired and re-queued for fee re-estimation.

Acceptance Criteria:
- Set TimeBounds(min_time=0, max_time=now + 300) on transaction builder.
- Expire unconfirmed transactions automatically after 5 minutes.
- Re-queue expired transactions for fee re-estimation.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.models.payment import (
    PaymentTransaction,
    TimeBounds,
    TRANSACTION_EXPIRY_WINDOW_SECONDS,
    TRANSACTION_TIMEOUT_MAX_SECONDS,
)
from app.models.orm.payment import PaymentTransactionORM

logger = logging.getLogger(__name__)


class TransactionExpiryService:
    """Service for managing payment transaction timeout bounds and expiry."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def apply_time_bounds(self, payment: PaymentTransaction) -> PaymentTransaction:
        """Apply default time bounds (now + 300s) to a payment transaction.

        Returns the updated payment with time_bounds_min/max set.
        """
        time_bounds = TimeBounds.default_for_transaction()
        payment.time_bounds_min = time_bounds.min_time
        payment.time_bounds_max = time_bounds.max_time
        return payment

    def check_and_expire(self, payment_id: str) -> Optional[PaymentTransaction]:
        """Check if a payment has exceeded its time bounds and expire it.

        If the transaction is still pending and its time_bounds_max has been
        exceeded, the transaction is:
          1. Marked as expired (fee_re_estimation_pending=True)
          2. Returned for fee re-estimation
        """
        from app.models.payment import PaymentStatus, validate_transition

        orm = (
            self.db.query(PaymentTransactionORM)
            .filter(PaymentTransactionORM.id == payment_id)
            .first()
        )
        if not orm:
            return None

        if orm.status != PaymentStatus.pending.value:
            return None  # only pending transactions can expire

        if orm.time_bounds_max <= 0:
            return None  # no time bounds set

        now = datetime.now(timezone.utc)
        max_time_dt = datetime.fromtimestamp(orm.time_bounds_max, tz=timezone.utc)

        if now <= max_time_dt:
            return None  # not yet expired

        # Transaction has exceeded its time bounds — expire it
        orm.expired_at = now
        orm.fee_re_estimation_pending = 1
        orm.last_retried_at = now
        orm.failure_taxonomy = "time_bounds_expired"
        orm.dead_letter_reason = (
            f"Transaction expired: time_bounds_max={orm.time_bounds_max} "
            f"exceeded at {now.isoformat()}"
        )
        self.db.commit()
        self.db.refresh(orm)

        from app.repositories.payment_repository import _orm_to_pydantic
        payment = _orm_to_pydantic(orm)

        logger.warning(
            "Payment %s expired after exceeding time_bounds_max=%d. "
            "Re-queued for fee re-estimation.",
            payment_id,
            orm.time_bounds_max,
        )
        return payment

    def expire_all_stale(self) -> List[str]:
        """Sweep all pending transactions and expire those past their time bounds.

        Returns list of expired payment IDs.
        """
        now = datetime.now(timezone.utc)
        cutoff_epoch = int(now.timestamp())

        stale = (
            self.db.query(PaymentTransactionORM)
            .filter(
                PaymentTransactionORM.status == "pending",
                PaymentTransactionORM.time_bounds_max > 0,
                PaymentTransactionORM.time_bounds_max < cutoff_epoch,
                PaymentTransactionORM.fee_re_estimation_pending == 0,
            )
            .all()
        )

        expired_ids: List[str] = []
        for orm in stale:
            orm.expired_at = now
            orm.fee_re_estimation_pending = 1
            orm.last_retried_at = now
            orm.failure_taxonomy = "time_bounds_expired"
            orm.dead_letter_reason = (
                f"Transaction expired: time_bounds_max={orm.time_bounds_max} "
                f"exceeded at {now.isoformat()}"
            )
            expired_ids.append(orm.id)

        if expired_ids:
            self.db.commit()
            logger.info(
                "Expired %d stale transactions for fee re-estimation: %s",
                len(expired_ids),
                expired_ids,
            )

        return expired_ids

    def requeue_for_fee_estimation(self, payment_id: str) -> Optional[PaymentTransaction]:
        """Re-queue an expired transaction with fresh time bounds for fee re-estimation.

        Resets the time bounds to now + 300s and clears the expiry flag,
        allowing the transaction to be submitted with an updated fee estimate.
        """
        from app.repositories.payment_repository import _orm_to_pydantic

        orm = (
            self.db.query(PaymentTransactionORM)
            .filter(PaymentTransactionORM.id == payment_id)
            .first()
        )
        if not orm:
            return None

        if not orm.fee_re_estimation_pending:
            return None

        # Apply fresh time bounds
        new_bounds = TimeBounds.default_for_transaction()
        orm.time_bounds_min = new_bounds.min_time
        orm.time_bounds_max = new_bounds.max_time
        orm.fee_re_estimation_pending = 0
        orm.expired_at = None
        orm.dead_letter_reason = None
        orm.failure_taxonomy = None
        orm.retry_count += 1
        self.db.commit()
        self.db.refresh(orm)

        payment = _orm_to_pydantic(orm)
        logger.info(
            "Re-queued payment %s for fee re-estimation with new time_bounds_max=%d",
            payment_id,
            new_bounds.max_time,
        )
        return payment
