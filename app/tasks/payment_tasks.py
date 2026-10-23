
import asyncio
import logging
from celery.exceptions import SoftTimeLimitExceeded
from app.core.config import settings
from app.tasks.celery_app import celery_app
from app.tasks.sla_tasks import DatabaseTask
from app.db.session import SessionLocal
from app.repositories.payment_repository import PaymentRepository
from app.services.contracts.sla_adapter import SLAAdapter
from app.services.stellar.balance_monitor import wallet_balance_monitor
from app.services.stellar.friendbot import FriendbotError, friendbot_service

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

@celery_app.task(
    name="app.tasks.payment_tasks.monitor_wallet_balances",
    max_retries=0,
)
def monitor_wallet_balances(address: str | None = None) -> dict:
    """Check the operator wallet balance against its thresholds (every 15 min).

    Alerts when XLM drops below ``WALLET_MIN_XLM_BALANCE`` (50) or the
    settlement asset below ``WALLET_MIN_USDC_BALANCE`` ($500), and caches the
    snapshot for ``GET /api/v1/health/detailed``.

    On testnet a wallet emptied by a network reset is additionally re-funded
    through Friendbot, so payouts recover without operator intervention.
    """
    if not settings.WALLET_BALANCE_MONITOR_ENABLED:
        logger.debug("Wallet balance monitor disabled; skipping check.")
        return {"status": "disabled"}

    health = asyncio.run(wallet_balance_monitor.run_check(address))
    summary = health.as_dict()

    if health.breaches and settings.STELLAR_FRIENDBOT_ENABLED and settings.supports_friendbot:
        summary["friendbot"] = _auto_fund_testnet_wallet(health.address)

    logger.info("Wallet balance check complete | %s", summary)
    return summary


def _auto_fund_testnet_wallet(address: str) -> dict:
    """Best-effort Friendbot top-up for a drained testnet wallet.

    Funding failures must not fail the monitoring run — the alert has
    already been raised by the time this is called.
    """
    try:
        result = asyncio.run(friendbot_service.fund_if_unfunded(address))
        return result.as_dict()
    except FriendbotError as exc:
        logger.warning(
            "Friendbot auto-funding skipped | address=%s reason=%s", address, exc.reason
        )
        return {"funded": False, "outcome": "error", "reason": exc.reason}
