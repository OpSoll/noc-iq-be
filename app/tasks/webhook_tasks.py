import json
import logging
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from uuid import UUID

from app.tasks.celery_app import celery_app, GuardedTask
from app.core.config import settings as cfg
from app.db.session import SessionLocal
from app.models.job import Job, JobStatus, JobType
from app.services.audit_log import audit_log

logger = logging.getLogger(__name__)


class WebhookDatabaseTask(GuardedTask):
    """Task base for webhook Celery tasks — tracks progress, supports quarantine,
    and manages lease heartbeats (BE-W5-047).

    BE-W5-054: Tasks inherit this base so retries can be quarantined after the
    per-delivery retry budget is exhausted.
    BE-W5-047: Lease heartbeats are recorded for long-running DR replay tasks.
    Inherits :class:`GuardedTask` for issue #531 execution time-limit cleanup.
    """

    abstract = True

    def get_db(self):
        return SessionLocal()

    def _get_job(self, db, celery_task_id: str) -> Optional[Job]:
        return db.query(Job).filter(Job.celery_task_id == celery_task_id).first()

    def _get_job_by_id(self, db, job_id: str) -> Optional[Job]:
        return db.query(Job).filter(Job.id == job_id).first()

    def _heartbeat(self, db, celery_task_id: str):
        """BE-W5-047: Extend the lease on a running job."""
        job = self._get_job(db, celery_task_id)
        if job and job.status == JobStatus.STARTED:
            job.heartbeat_at = datetime.utcnow()
            job.lease_expires_at = datetime.utcnow() + timedelta(
                seconds=cfg.JOB_LEASE_TIMEOUT_SECONDS
            )
            try:
                db.commit()
            except Exception:
                db.rollback()

    def _mark_started(self, db, celery_task_id: str):
        """BE-W5-047: Initialise lease on task start."""
        job = self._get_job(db, celery_task_id)
        if job:
            job.status = JobStatus.STARTED
            job.started_at = datetime.utcnow()
            job.heartbeat_at = datetime.utcnow()
            job.lease_expires_at = datetime.utcnow() + timedelta(
                seconds=cfg.JOB_LEASE_TIMEOUT_SECONDS
            )
            try:
                db.commit()
            except Exception:
                db.rollback()

    def _update_progress(
        self,
        db,
        celery_task_id: str,
        progress: float,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        job = self._get_job(db, celery_task_id)
        if job:
            job.progress = min(progress, 99.0)
            if details:
                job.progress_details = details
            try:
                db.commit()
            except Exception:
                db.rollback()


@celery_app.task(
    bind=True,
    name="app.tasks.webhook_tasks.dispatch_webhook_delivery",
    max_retries=5,
    default_retry_delay=30,
)
def dispatch_webhook_delivery(self, delivery_id: str) -> Dict[str, Any]:
    """Deliver a single WebhookDelivery record asynchronously.

    Issue #302: Delivery is dispatched with partition awareness.
    Backpressure is checked at the service layer before each attempt.
    """
    db = SessionLocal()
    try:
        from app.services.webhook_service import dispatch_delivery
        dispatch_delivery(db, UUID(delivery_id))
        logger.info("Webhook delivery %s dispatched.", delivery_id)
        return {"delivery_id": delivery_id, "dispatched": True}
    except Exception as exc:
        error_msg = str(exc)
        logger.exception("Failed to dispatch webhook delivery %s: %s", delivery_id, error_msg)

        # BE-W5-048: Check if retries are exhausted before raising to Celery
        if self.request.retries >= self.max_retries:
            # Retries exhausted — route to dead-letter terminal status
            job = db.query(Job).filter(Job.celery_task_id == self.request.id).first()
            if job:
                job.status = JobStatus.DEAD_LETTER
                job.dead_letter_reason = (
                    f"Max retries exhausted ({self.max_retries}). Last error: {error_msg}"
                )
                job.dead_letter_at = datetime.utcnow()
                job.finished_at = datetime.utcnow()
                job.error = error_msg
                job.error_code = "WEBHOOK_RETRIES_EXHAUSTED"
                job.error_retryable = False
                try:
                    db.commit()
                except Exception:
                    db.rollback()
                audit_log.log_event(
                    db,
                    event_type="job_dead_lettered",
                    details={
                        "delivery_id": delivery_id,
                        "job_type": JobType.WEBHOOK_DISPATCH.value,
                        "retry_count": self.request.retries + 1,
                        "max_retries": self.max_retries,
                        "error": error_msg,
                    },
                )
            return {"delivery_id": delivery_id, "dispatched": False, "dead_lettered": True}

        # Log retry attempt if we have retries left
        if self.request.retries < self.max_retries:
            audit_log.log_event(
                db,
                event_type="webhook_retried",
                details={
                    "delivery_id": delivery_id,
                    "retry_count": self.request.retries + 1,
                    "error": error_msg,
                }
            )

        raise self.retry(exc=exc)
    finally:
        db.close()


@celery_app.task(
    name="app.tasks.webhook_tasks.retry_pending_webhook_deliveries",
)
def retry_pending_webhook_deliveries() -> Dict[str, Any]:
    """
    Periodic beat task: finds all due RETRYING deliveries and re-dispatches them.
    Registered in celery_app.conf.beat_schedule to run every 60 seconds.
    """
    db = SessionLocal()
    try:
        from app.services.webhook_service import retry_pending_deliveries
        count = retry_pending_deliveries(db)
        logger.info("Retried %d pending webhook deliveries.", count)
        return {"retried": count}
    finally:
        db.close()


@celery_app.task(
    name="app.tasks.webhook_tasks.dispatch_partitioned_delivery",
    autoretry_for=(Exception,),
    max_retries=3,
    default_retry_delay=60,
)
def dispatch_partitioned_delivery(delivery_id: str, partition_id: int) -> Dict[str, Any]:
    """Partition-aware webhook dispatch.

    Issue #302: Dispatches a delivery within a specific partition.
    Checks backpressure before attempting and defers to a retry loop
    if the partition is saturated.

    SLA/payment-critical partitions (priority partitions) bypass
    backpressure checks so they are never starved.
    """
    from app.core.config import settings as cfg
    from app.services.webhook_service import (
        dispatch_delivery,
        is_backpressured,
        _get_partition_pending_count,
    )

    # Check backpressure (except for priority SLA partition)
    if partition_id != cfg.WEBHOOK_SLA_PRIORITY_PARTITION:
        if is_backpressured(partition_id):
            pending = _get_partition_pending_count(partition_id)
            logger.warning(
                "Partition %d backpressured (%d pending). Deferring delivery %s.",
                partition_id, pending, delivery_id,
            )
            raise Exception(f"Partition {partition_id} backpressured ({pending} pending)")

    db = SessionLocal()
    try:
        dispatch_delivery(db, UUID(delivery_id))
        logger.info(
            "Partitioned delivery %s dispatched on partition %d.",
            delivery_id, partition_id,
        )
        return {
            "delivery_id": delivery_id,
            "partition_id": partition_id,
            "dispatched": True,
        }
    except Exception as exc:
        logger.exception(
            "Failed to dispatch partitioned delivery %s on partition %d: %s",
            delivery_id, partition_id, exc,
        )
        raise
    finally:
        db.close()


@celery_app.task(
    bind=True,
    base=WebhookDatabaseTask,
    name="app.tasks.webhook_tasks.recover_webhooks_in_window",
    max_retries=1,
    default_retry_delay=60,
)
def recover_webhooks_in_window(
    self: WebhookDatabaseTask,
    job_id: str,
    start_iso: str,
    end_iso: str,
) -> Dict[str, Any]:
    """Replay webhook deliveries whose ``event_timestamp`` falls in [start, end].

    BE-W5-045: Webhook disaster-recovery replay.
      * Bounded time window (caller-supplied).
      * Idempotent because ``replay_dead_letter_delivery`` preserves the
        deterministic ``idempotency_key`` and ``event_timestamp``.
      * Progress is written to the ``Job`` row so an operator can resume /
        audit by polling ``GET /jobs/{id}``.
    """
    from app.services.webhook_service import recover_deliveries_in_window

    start_dt = datetime.fromisoformat(start_iso)
    end_dt = datetime.fromisoformat(end_iso)

    db = self.get_db()
    try:
        job = self._get_job_by_id(db, job_id)
        if not job:
            logger.error("BE-W5-045: DR replay job %s not found", job_id)
            return {"job_id": job_id, "replayed": 0, "status": "missing_job"}

        job.status = JobStatus.STARTED
        job.started_at = datetime.utcnow()
        db.commit()

        result = recover_deliveries_in_window(
            db,
            start_time=start_dt,
            end_time=end_dt,
            on_progress=lambda replayed, total: self._update_progress(
                db,
                job_id,
                replayed / total * 100 if total else 0,
                {
                    "stage": "replaying",
                    "replayed": replayed,
                    "total": total,
                    "start": start_iso,
                    "end": end_iso,
                },
            ),
        )
        job.status = JobStatus.SUCCESS
        job.result = json.dumps(result)
        job.progress = 100.0
        job.finished_at = datetime.utcnow()
        db.commit()
        logger.info(
            "BE-W5-045: DR replay job=%s window=[%s,%s] replayed=%d skipped=%d",
            job_id, start_iso, end_iso,
            result.get("replayed", 0), result.get("skipped", 0),
        )
        return result
    except Exception as exc:
        logger.exception("BE-W5-045: DR replay job %s failed: %s", job_id, exc)
        try:
            db.rollback()
            job = self._get_job_by_id(db, job_id)
            if job:
                job.status = JobStatus.FAILURE
                job.error = str(exc)
                job.finished_at = datetime.utcnow()
                db.commit()
        except Exception:
            db.rollback()
        # Re-raise so Celery can record failure (max_retries=1 minimises loops).
        raise
    finally:
        db.close()


@celery_app.task(
    bind=True,
    name="app.tasks.webhook_tasks.trigger_sla_violation_async",
    max_retries=3,
    default_retry_delay=15,
)
def trigger_sla_violation_async(
    self, sla_data: Dict[str, Any], event: str = "sla.violation"
) -> Dict[str, Any]:
    """
    Async task wrapper around webhook_service.trigger_sla_violation_webhooks.
    Called from SLA computation tasks to avoid blocking.
    """
    db = SessionLocal()
    try:
        from app.models.webhook import WebhookEvent
        from app.services.webhook_service import trigger_sla_violation_webhooks

        deliveries = trigger_sla_violation_webhooks(
            db, sla_data=sla_data, event=WebhookEvent(event)
        )
        logger.info("Triggered %d webhook deliveries for event=%s.", len(deliveries), event)
        return {"triggered": len(deliveries), "event": event}
    except Exception as exc:
        error_msg = str(exc)
        logger.exception("trigger_sla_violation_async failed: %s", error_msg)

        # Log retry attempt if we have retries left
        if self.request.retries < self.max_retries:
            audit_log.log_event(
                db,
                event_type="sla_violation_webhook_retried",
                details={
                    "sla_data": sla_data,
                    "event": event,
                    "retry_count": self.request.retries + 1,
                    "error": error_msg,
                }
            )

        raise self.retry(exc=exc)
    finally:
        db.close()
