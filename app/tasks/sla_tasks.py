import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from celery import Task

from app.tasks.celery_app import celery_app
from app.db.session import SessionLocal
from app.models.job import Job, JobStatus, JobType
from app.models.webhook import WebhookEvent

logger = logging.getLogger(__name__)


class DatabaseTask(Task):
    """Base task that provides a scoped DB session and updates Job records."""

    abstract = True
    _db = None

    def get_db(self):
        return SessionLocal()

    def _get_job(self, db, celery_task_id: str) -> Optional[Job]:
        return db.query(Job).filter(Job.celery_task_id == celery_task_id).first()

    def _mark_started(self, db, celery_task_id: str):
        job = self._get_job(db, celery_task_id)
        if job:
            job.status = JobStatus.STARTED
            job.started_at = datetime.utcnow()
            db.commit()

    def _mark_success(self, db, celery_task_id: str, result: Any):
        job = self._get_job(db, celery_task_id)
        if job:
            job.status = JobStatus.SUCCESS
            job.result = json.dumps(result)
            job.progress = 100.0
            job.finished_at = datetime.utcnow()
            db.commit()

    def _mark_failure(self, db, celery_task_id: str, error: str):
        job = self._get_job(db, celery_task_id)
        if job:
            job.status = JobStatus.FAILURE
            job.error = error
            job.finished_at = datetime.utcnow()
            db.commit()

    def _update_progress(self, db, celery_task_id: str, progress: float):
        job = self._get_job(db, celery_task_id)
        if job:
            job.progress = min(progress, 99.0)
            db.commit()


@celery_app.task(
    bind=True,
    base=DatabaseTask,
    name="app.tasks.sla_tasks.compute_sla_for_device",
    max_retries=3,
    default_retry_delay=30,
)
def compute_sla_for_device(self: DatabaseTask, device_id: str, period: str) -> Dict[str, Any]:
    """
    Compute SLA metrics for a single device over a given period.
    Triggers SLA violation webhooks if thresholds are breached.
    """
    db = self.get_db()
    try:
        self._mark_started(db, self.request.id)
        logger.info("Starting SLA computation for device=%s period=%s", device_id, period)

        # ------------------------------------------------------------------ #
        # SLA computation logic — replace with actual domain implementation   #
        # ------------------------------------------------------------------ #
        from app.services.sla_service import compute_device_sla  # type: ignore
        result = compute_device_sla(db, device_id=device_id, period=period)
        self._update_progress(db, self.request.id, 70.0)

        # Check for violations and dispatch webhooks
        if result.get("is_violated"):
            from app.services.webhook_service import trigger_sla_violation_webhooks
            trigger_sla_violation_webhooks(
                db,
                sla_data={
                    "device_id": device_id,
                    "period": period,
                    **result,
                },
                event=WebhookEvent.SLA_VIOLATION,
            )

        self._mark_success(db, self.request.id, result)
        logger.info("SLA computation complete for device=%s", device_id)
        return result

    except Exception as exc:
        error_msg = str(exc)
        logger.exception("SLA computation failed for device=%s: %s", device_id, error_msg)
        self._mark_failure(db, self.request.id, error_msg)
        raise self.retry(exc=exc)
    finally:
        db.close()


@celery_app.task(
    bind=True,
    base=DatabaseTask,
    name="app.tasks.sla_tasks.compute_bulk_sla",
    max_retries=2,
    default_retry_delay=60,
)
def compute_bulk_sla(self: DatabaseTask, device_ids: List[str], period: str) -> Dict[str, Any]:
    """
    Compute SLA for multiple devices. Dispatches individual tasks per device
    and tracks overall progress.
    """
    db = self.get_db()
    try:
        self._mark_started(db, self.request.id)
        total = len(device_ids)
        logger.info("Starting bulk SLA computation for %d devices, period=%s", total, period)

        results = []
        violations = []

        for idx, device_id in enumerate(device_ids, start=1):
            try:
                from app.services.sla_service import compute_device_sla  # type: ignore
                result = compute_device_sla(db, device_id=device_id, period=period)
                results.append({"device_id": device_id, "result": result})

                if result.get("is_violated"):
                    violations.append(device_id)
                    from app.services.webhook_service import trigger_sla_violation_webhooks
                    trigger_sla_violation_webhooks(
                        db,
                        sla_data={"device_id": device_id, "period": period, **result},
                        event=WebhookEvent.SLA_VIOLATION,
                    )

            except Exception as device_exc:
                logger.warning("SLA failed for device=%s: %s", device_id, device_exc)
                results.append({"device_id": device_id, "error": str(device_exc)})

            progress = (idx / total) * 100
            self._update_progress(db, self.request.id, progress)

        summary = {
            "total": total,
            "violations": len(violations),
            "violated_devices": violations,
            "results": results,
        }
        self._mark_success(db, self.request.id, summary)
        logger.info("Bulk SLA computation complete. Violations: %d/%d", len(violations), total)
        return summary

    except Exception as exc:
        error_msg = str(exc)
        logger.exception("Bulk SLA computation failed: %s", error_msg)
        self._mark_failure(db, self.request.id, error_msg)
        raise self.retry(exc=exc)
    finally:
        db.close()


def enqueue_sla_computation(
    db,
    device_id: str,
    period: str,
    job_type: JobType = JobType.SLA_COMPUTATION,
) -> Job:
    """
    Enqueue an SLA computation task and create a Job record for tracking.
    Returns the Job before the Celery task ID is known — updated after dispatch.
    """
    from app.models.job import Job, JobType  # local import avoids circular deps

    task_result = compute_sla_for_device.apply_async(
        kwargs={"device_id": device_id, "period": period}
    )

    job = Job(
        celery_task_id=task_result.id,
        job_type=job_type,
        payload=json.dumps({"device_id": device_id, "period": period}),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def enqueue_bulk_sla_computation(db, device_ids: List[str], period: str) -> Job:
    """Enqueue a bulk SLA computation task and return the tracking Job."""
    from app.models.job import Job, JobType

    task_result = compute_bulk_sla.apply_async(
        kwargs={"device_ids": device_ids, "period": period}
    )

    job = Job(
        celery_task_id=task_result.id,
        job_type=JobType.BULK_SLA_COMPUTATION,
        payload=json.dumps({"device_ids": device_ids, "period": period}),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job
