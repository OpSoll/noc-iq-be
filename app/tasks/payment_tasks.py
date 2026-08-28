
import asyncio
import logging
from celery.exceptions import SoftTimeLimitExceeded
from app.tasks.celery_app import celery_app
from app.tasks.sla_tasks import DatabaseTask
from app.db.session import SessionLocal
from app.repositories.payment_repository import PaymentRepository
from app.services.contracts.sla_adapter import SLAAdapter

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    base=DatabaseTask,
    name="app.tasks.payment_tasks.verify_payment_transactions",
    max_retries=3,
    default_retry_delay=60,
)
def verify_payment_transactions(self: DatabaseTask):
    """
    Verify the status of pending payment transactions.
    """
    db = self.get_db()
    try:
        self._mark_started(db, self.request.id)
        logger.info("Starting payment transaction verification task")

        payment_repo = PaymentRepository(db)
        pending_payments = payment_repo.list(status="pending", page_size=1000)[0]

        if not pending_payments:
            logger.info("No pending payments to verify.")
            self._mark_success(db, self.request.id, {"verified": 0, "confirmed": 0, "failed": 0})
            return

        sla_adapter = SLAAdapter()
        confirmed_count = 0
        failed_count = 0

        for payment in pending_payments:
            try:
                tx_status = asyncio.run(sla_adapter.get_transaction_status(payment.transaction_hash))

                if tx_status == "confirmed":
                    payment_repo.reconcile(payment.id, "confirmed")
                    confirmed_count += 1
                elif tx_status == "failed":
                    payment_repo.reconcile(payment.id, "failed")
                    failed_count += 1

            except Exception as e:
                logger.error(
                    f"Failed to verify transaction {payment.transaction_hash}: {e}"
                )

        summary = {
            "verified": len(pending_payments),
            "confirmed": confirmed_count,
            "failed": failed_count,
        }
        self._mark_success(db, self.request.id, summary)
        logger.info(f"Payment transaction verification task finished: {summary}")

    except SoftTimeLimitExceeded as exc:
        logger.warning("Payment transaction verification task hit soft time limit")
        self._mark_failure(
            db,
            self.request.id,
            f"SoftTimeLimitExceeded: {exc}",
            error_code="SOFT_TIME_LIMIT",
            error_retryable=False,
        )
        raise

    except Exception as exc:
        error_msg = str(exc)
        logger.exception("Payment transaction verification task failed: %s", error_msg)
        self._mark_failure(db, self.request.id, error_msg, error_code="TASK_FAILED", error_retryable=True)
        raise self.retry(exc=exc)

    finally:
        db.close()