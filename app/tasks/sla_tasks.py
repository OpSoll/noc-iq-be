import hashlib
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from celery.exceptions import SoftTimeLimitExceeded

from app.tasks.celery_app import celery_app, GuardedTask
from app.core.config import settings as cfg
from app.db.session import SessionLocal
from app.services.task_lock import RedisTaskLock
from app.models.job import Job, JobStatus, JobType
from app.models.webhook import WebhookEvent
from app.repositories.payment_repository import PaymentRepository
from app.services.audit_log import audit_log
from app.utils.analytics_exporter import AnalyticsExporter
from app.utils.correlation import set_correlation_id
from app.utils.logging import get_structured_logger

logger = logging.getLogger(__name__)
task_logger = get_structured_logger("sla_tasks")

# Issue #538: bulk SLA computation chunk size. Batches larger than this are
# split into parallel chunks via Celery ``chunks()`` so a single worker is
# never blocked for minutes on a 10,000-device batch.
SLA_BULK_CHUNK_SIZE = 50


def _bulk_sla_lock_job_id(call_args) -> str:
    """Deterministic lock job id for a bulk SLA batch (Issue #533).

    Derived from a SHA-256 digest of the sorted device list + period so two
    identical batch triggers map to the same ``lock:task:*`` key and only
    one of them executes.
    """
    device_ids = sorted(call_args.get("device_ids") or [])
    period = call_args.get("period") or ""
    digest = hashlib.sha256(
        json.dumps(device_ids, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    return f"bulk_sla:{digest}:{period}"


def _hash_job_payload(job: Job) -> str:
    """Deterministic SHA256 over the canonicalised job payload.

    BE-W5-054: payload hash used to identify poison messages whose input
    keeps crashing workers across retries. The hash is computed over a
    sorted, whitespace-stripped JSON serialisation so trivial formatting
    differences do not produce different fingerprints.
    """
    raw = job.payload or ""
    try:
        obj = json.loads(raw)
        canonical = json.dumps(obj, sort_keys=True, separators=(",", ":"))
    except (json.JSONDecodeError, TypeError):
        canonical = raw.strip()
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class DatabaseTask(GuardedTask):
    """Base task that provides a scoped DB session, updates Job records,
    and manages lease heartbeats for BE-W5-047.

    Inherits :class:`GuardedTask` for issue #531 execution time-limit
    cleanup hooks."""

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
            # BE-W5-047: Initialize lease on start
            job.heartbeat_at = datetime.utcnow()
            job.lease_expires_at = datetime.utcnow() + timedelta(seconds=cfg.JOB_LEASE_TIMEOUT_SECONDS)
            db.commit()

    def _heartbeat(self, db, celery_task_id: str):
        """BE-W5-047: Extend the lease on a running job."""
        job = self._get_job(db, celery_task_id)
        if job and job.status == JobStatus.STARTED:
            job.heartbeat_at = datetime.utcnow()
            job.lease_expires_at = datetime.utcnow() + timedelta(seconds=cfg.JOB_LEASE_TIMEOUT_SECONDS)
            db.commit()

    def _mark_success(self, db, celery_task_id: str, result: Any):
        job = self._get_job(db, celery_task_id)
        if job:
            job.status = JobStatus.SUCCESS
            job.result = json.dumps(result)
            job.progress = 100.0
            job.finished_at = datetime.utcnow()
            # BE-W5-047: Clear lease on completion
            job.lease_expires_at = None
            job.heartbeat_at = None
            db.commit()

    def _mark_failure(self, db, celery_task_id: str, error: str, error_code: Optional[str] = None, error_retryable: Optional[bool] = None):
        job = self._get_job(db, celery_task_id)
        if not job:
            return
        # BE-W5-054: bucket retry_count so the QUARANTINED boundary is invariant.
        attempts = (job.retry_count or 0) + 1
        # BE-W5-054: poison-message quarantine
        if attempts >= max(job.max_retries, 0):
            # BE-W5-048: if dead-letter is enabled, route exhausted jobs there
            if cfg.JOB_RETRY_DEAD_LETTER_ENABLED:
                job.status = JobStatus.DEAD_LETTER
                job.dead_letter_reason = f"Max retries exhausted ({job.max_retries}). Last error: {error}"
                job.dead_letter_at = datetime.utcnow()
                job.finished_at = datetime.utcnow()
                job.retry_count = attempts
                job.error = error
                job.error_code = error_code or "DEAD_LETTER"
                job.error_retryable = False
                logger.error(
                    "BE-W5-048: dead-lettering job %s type=%s after %d attempts: %s",
                    job.id, job.job_type.value, attempts, error,
                )
                audit_log.log_event(
                    db,
                    event_type="job_dead_lettered",
                    details={
                        "job_id": str(job.id),
                        "celery_task_id": celery_task_id,
                        "job_type": job.job_type.value,
                        "retry_count": attempts,
                        "max_retries": job.max_retries,
                        "retry_class": job.retry_class,
                        "error": error,
                        "error_code": error_code,
                    },
                )
                db.commit()
                return

            payload_hash = _hash_job_payload(job)
            job.payload_hash = payload_hash
            job.quarantine_reason = error
            job.quarantined_at = datetime.utcnow()
            job.status = JobStatus.QUARANTINED
            job.retry_count = attempts
            job.finished_at = datetime.utcnow()
            job.error_code = error_code or "QUARANTINED"
            job.error_retryable = False
            logger.error(
                "BE-W5-054: quarantining job %s type=%s payload_hash=%s after "
                "%d attempts: %s",
                job.id, job.job_type.value, payload_hash, attempts, error,
            )
            audit_log.log_event(
                db,
                event_type="job_quarantined",
                details={
                    "job_id": str(job.id),
                    "celery_task_id": celery_task_id,
                    "job_type": job.job_type.value,
                    "retry_count": attempts,
                    "max_retries": job.max_retries,
                    "payload_hash": payload_hash,
                    "error": error,
                },
            )
            db.commit()
            return

        job.retry_count = attempts
        job.status = JobStatus.FAILURE
        job.error = error
        job.error_code = error_code
        job.error_retryable = error_retryable if error_retryable is not None else False
        job.finished_at = datetime.utcnow()
        db.commit()

    def _update_progress(self, db, celery_task_id: str, progress: float, details: Optional[Dict[str, Any]] = None):
        job = self._get_job(db, celery_task_id)
        if job:
            job.progress = min(progress, 99.0)
            if details:
                job.progress_details = details
            db.commit()

    def _add_partial_result(self, db, celery_task_id: str, item_id: str, result: Any):
        """Add a partial result for bulk operations."""
        job = self._get_job(db, celery_task_id)
        if job:
            if not job.partial_results:
                job.partial_results = {}
            job.partial_results[item_id] = result
            db.commit()

    def _add_item_error(self, db, celery_task_id: str, item_id: str, error: str):
        """Add an error for a specific item in bulk operations."""
        job = self._get_job(db, celery_task_id)
        if job:
            if not job.per_item_errors:
                job.per_item_errors = {}
            job.per_item_errors[item_id] = error
            db.commit()

    def _log_retry(self, db, celery_task_id: str, retry_count: int, error: str):
        """Log job retry events for audit purposes."""
        job = self._get_job(db, celery_task_id)
        if job:
            audit_log.log_event(
                db,
                event_type="job_retried",
                details={
                    "job_id": str(job.id),
                    "celery_task_id": celery_task_id,
                    "job_type": job.job_type.value,
                    "retry_count": retry_count,
                    "error": error,
                    "payload": job.payload
                }
            )


@celery_app.task(
    bind=True,
    base=DatabaseTask,
    name="app.tasks.sla_tasks.compute_sla_for_device",
    max_retries=3,
    default_retry_delay=30,
)
@RedisTaskLock("sla:{device_id}:{period}")  # Issue #533
def compute_sla_for_device(self: DatabaseTask, device_id: str, period: str, correlation_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Compute SLA metrics for a single device over a given period.
    Triggers SLA violation webhooks if thresholds are breached.
    """
    # Set correlation ID for this task execution
    if correlation_id:
        set_correlation_id(correlation_id)
    
    db = self.get_db()
    try:
        self._mark_started(db, self.request.id)
        task_logger.info(
            "Starting SLA computation",
            device_id=device_id,
            period=period,
            celery_task_id=self.request.id,
            correlation_id=correlation_id
        )

        # ------------------------------------------------------------------ #
        # SLA computation logic — replace with actual domain implementation   #
        # ------------------------------------------------------------------ #
        from app.services.sla_service import compute_device_sla  # type: ignore
        
        # Update progress with structured details
        self._update_progress(db, self.request.id, 30.0, {
            "stage": "data_collection",
            "device_id": device_id,
            "period": period
        })
        
        result = compute_device_sla(db, device_id=device_id, period=period)
        
        self._update_progress(db, self.request.id, 70.0, {
            "stage": "sla_computation_complete",
            "device_id": device_id,
            "period": period,
            "is_violated": result.get("is_violated", False)
        })

        # Check for violations and dispatch webhooks
        if result.get("is_violated"):
            self._update_progress(db, self.request.id, 85.0, {
                "stage": "triggering_webhooks",
                "device_id": device_id,
                "period": period,
                "violation_detected": True
            })
            
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

        self._update_progress(db, self.request.id, 95.0, {
            "stage": "finalizing",
            "device_id": device_id,
            "period": period
        })

        self._mark_success(db, self.request.id, result)
        logger.info("SLA computation complete for device=%s", device_id)
        return result

    except SoftTimeLimitExceeded as exc:
        # Issue #531: log graceful cleanup and surface as a timeout failure.
        # Do not retry — the task keeps exceeding its time limit.
        logger.warning(
            "SLA computation for device=%s hit soft time limit — cleaning up gracefully",
            device_id,
        )
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
        logger.exception("SLA computation failed for device=%s: %s", device_id, error_msg)

        # BE-W5-050: Classify error for typed envelope
        error_code = "SLA_COMPUTATION_ERROR"
        error_retryable = self.request.retries < self.max_retries

        # Log retry attempt if we have retries left
        if self.request.retries < self.max_retries:
            self._log_retry(db, self.request.id, self.request.retries + 1, error_msg)

        self._mark_failure(db, self.request.id, error_msg, error_code=error_code, error_retryable=error_retryable)
        raise self.retry(exc=exc)
    finally:
        db.close()


@celery_app.task(
    bind=True,
    base=DatabaseTask,
    name="app.tasks.sla_tasks.compute_sla_chunk",
    max_retries=2,
    default_retry_delay=60,
)
def compute_sla_chunk(
    self: DatabaseTask,
    chunk_device_ids: List[str],
    period: str,
    job_task_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Process a single chunk of device IDs (≤ SLA_BULK_CHUNK_SIZE items).

    Issue #538: dispatched in parallel by ``compute_bulk_sla`` via Celery
    ``chunks()`` so large SLA batches no longer block a single worker for
    minutes. Returns a per-chunk summary the parent aggregates.

    When ``job_task_id`` is the parent bulk job's Celery task ID, per-device
    partial results / item errors are written back to the parent ``Job`` row
    so progress tracking survives chunking.
    """
    db = self.get_db()
    try:
        results = []
        violations = []
        processed_count = 0
        error_count = 0
        total = len(chunk_device_ids)

        for idx, device_id in enumerate(chunk_device_ids, start=1):
            try:
                from app.services.sla_service import compute_device_sla  # type: ignore

                result = compute_device_sla(db, device_id=device_id, period=period)
                results.append({"device_id": device_id, "result": result})

                if job_task_id:
                    self._add_partial_result(db, job_task_id, device_id, result)

                if result.get("is_violated"):
                    violations.append(device_id)
                    from app.services.webhook_service import trigger_sla_violation_webhooks

                    trigger_sla_violation_webhooks(
                        db,
                        sla_data={"device_id": device_id, "period": period, **result},
                        event=WebhookEvent.SLA_VIOLATION,
                    )
                processed_count += 1

            except Exception as device_exc:
                logger.warning("SLA failed for device=%s: %s", device_id, device_exc)
                results.append({"device_id": device_id, "error": str(device_exc)})

                if job_task_id:
                    self._add_item_error(db, job_task_id, device_id, str(device_exc))
                error_count += 1

            if job_task_id and total:
                progress = (idx / total) * 100
                self._update_progress(db, job_task_id, progress, {
                    "stage": "processing_chunk",
                    "current_device": device_id,
                    "processed_count": processed_count,
                    "error_count": error_count,
                    "chunk_size": total,
                    "progress_percentage": round(progress, 2),
                })

        return {
            "total": total,
            "violations": len(violations),
            "violated_devices": violations,
            "processed_count": processed_count,
            "error_count": error_count,
            "results": results,
        }
    except Exception as exc:
        logger.exception("SLA chunk computation failed: %s", exc)
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
@RedisTaskLock(lock_key=_bulk_sla_lock_job_id)  # Issue #533
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

        # Initialize progress tracking
        self._update_progress(db, self.request.id, 5.0, {
            "stage": "initialization",
            "total_devices": total,
            "period": period
        })

        # Issue #538: chunk large batches into parallel tasks of at most
        # SLA_BULK_CHUNK_SIZE (50) devices so a single worker is never
        # blocked for minutes on a huge batch. Small batches keep the
        # sequential in-process path.
        if total > SLA_BULK_CHUNK_SIZE:
            device_chunks = [
                device_ids[i : i + SLA_BULK_CHUNK_SIZE]
                for i in range(0, total, SLA_BULK_CHUNK_SIZE)
            ]
            self._update_progress(db, self.request.id, 10.0, {
                "stage": "dispatching_chunks",
                "total_devices": total,
                "chunk_size": SLA_BULK_CHUNK_SIZE,
                "chunk_count": len(device_chunks),
                "period": period,
            })
            logger.info(
                "Bulk SLA computation chunking %d devices into %d chunks "
                "of %d (period=%s)",
                total, len(device_chunks), SLA_BULK_CHUNK_SIZE, period,
            )

            # Dispatch one chunk task per 50-device slice via Celery
            # ``chunks()``; chunks run in parallel across worker nodes.
            chunk_group = compute_sla_chunk.chunks(
                [(chunk, period, self.request.id) for chunk in device_chunks],
                1,
            ).apply_async()
            chunk_summaries = chunk_group.join(timeout=3600, propagate=True)

            # Each chunk subtask's result is a list of per-invocation
            # results (one invocation per chunk), so flatten before
            # aggregating.
            flat_summaries = []
            for item in chunk_summaries:
                if isinstance(item, list):
                    flat_summaries.extend(item)
                else:
                    flat_summaries.append(item)

            results = []
            violations = []
            processed_count = 0
            error_count = 0
            for chunk_summary in flat_summaries:
                processed_count += chunk_summary.get("processed_count", 0)
                error_count += chunk_summary.get("error_count", 0)
                violations.extend(chunk_summary.get("violated_devices", []))
                results.extend(chunk_summary.get("results", []))

            self._update_progress(db, self.request.id, 95.0, {
                "stage": "finalizing",
                "total_devices": total,
                "processed_count": processed_count,
                "error_count": error_count,
                "violations_found": len(violations),
                "chunk_count": len(device_chunks),
            })

            summary = {
                "total": total,
                "violations": len(violations),
                "violated_devices": violations,
                "processed_count": processed_count,
                "error_count": error_count,
                "results": results,
                "chunked": True,
                "chunk_count": len(device_chunks),
                "chunk_size": SLA_BULK_CHUNK_SIZE,
            }

            self._mark_success(db, self.request.id, summary)
            logger.info(
                "Bulk SLA computation complete (chunked). Violations: %d/%d, "
                "Errors: %d, Chunks: %d",
                len(violations), total, error_count, len(device_chunks),
            )
            return summary

        results = []
        violations = []
        processed_count = 0
        error_count = 0

        for idx, device_id in enumerate(device_ids, start=1):
            try:
                from app.services.sla_service import compute_device_sla  # type: ignore
                result = compute_device_sla(db, device_id=device_id, period=period)
                results.append({"device_id": device_id, "result": result})
                
                # Store partial result
                self._add_partial_result(db, self.request.id, device_id, result)

                if result.get("is_violated"):
                    violations.append(device_id)
                    from app.services.webhook_service import trigger_sla_violation_webhooks
                    trigger_sla_violation_webhooks(
                        db,
                        sla_data={"device_id": device_id, "period": period, **result},
                        event=WebhookEvent.SLA_VIOLATION,
                    )
                
                processed_count += 1

            except Exception as device_exc:
                logger.warning("SLA failed for device=%s: %s", device_id, device_exc)
                results.append({"device_id": device_id, "error": str(device_exc)})
                
                # Store per-item error
                self._add_item_error(db, self.request.id, device_id, str(device_exc))
                error_count += 1

            # Update progress with detailed information
            progress = (idx / total) * 100
            self._update_progress(db, self.request.id, progress, {
                "stage": "processing_devices",
                "current_device": device_id,
                "processed_count": processed_count,
                "error_count": error_count,
                "total_devices": total,
                "violations_found": len(violations),
                "progress_percentage": round(progress, 2)
            })

        # Final summary with structured progress
        self._update_progress(db, self.request.id, 95.0, {
            "stage": "finalizing",
            "total_devices": total,
            "processed_count": processed_count,
            "error_count": error_count,
            "violations_found": len(violations)
        })
        
        summary = {
            "total": total,
            "violations": len(violations),
            "violated_devices": violations,
            "processed_count": processed_count,
            "error_count": error_count,
            "results": results,
        }
        
        self._mark_success(db, self.request.id, summary)
        logger.info("Bulk SLA computation complete. Violations: %d/%d, Errors: %d", len(violations), total, error_count)
        return summary

    except SoftTimeLimitExceeded as exc:
        # Issue #531: log graceful cleanup; do not retry a timing-out task.
        logger.warning(
            "Bulk SLA computation hit soft time limit — cleaning up gracefully",
        )
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
        logger.exception("Bulk SLA computation failed: %s", error_msg)

        error_code = "BULK_SLA_COMPUTATION_ERROR"
        error_retryable = self.request.retries < self.max_retries

        if self.request.retries < self.max_retries:
            self._log_retry(db, self.request.id, self.request.retries + 1, error_msg)

        self._mark_failure(db, self.request.id, error_msg, error_code=error_code, error_retryable=error_retryable)
        raise self.retry(exc=exc)
    finally:
        db.close()


def enqueue_sla_computation(
    db,
    device_id: str,
    period: str,
    job_type: JobType = JobType.SLA_COMPUTATION,
    correlation_id: Optional[str] = None,
) -> Job:
    """
    Enqueue an SLA computation task and create a Job record for tracking.
    Returns the Job before the Celery task ID is known — updated after dispatch.
    """
    from app.models.job import Job, JobType  # local import avoids circular deps

    payload = {"device_id": device_id, "period": period}
    if correlation_id:
        payload["correlation_id"] = correlation_id

    task_result = compute_sla_for_device.apply_async(
        kwargs=payload
    )

    job = Job(
        celery_task_id=task_result.id,
        job_type=job_type,
        payload=json.dumps(payload),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def enqueue_bulk_sla_computation(db, device_ids: List[str], period: str, correlation_id: Optional[str] = None) -> Job:
    """Enqueue a bulk SLA computation task and return the tracking Job."""
    from app.models.job import Job, JobType

    payload = {"device_ids": device_ids, "period": period}
    if correlation_id:
        payload["correlation_id"] = correlation_id

    task_result = compute_bulk_sla.apply_async(
        kwargs=payload
    )

    job = Job(
        celery_task_id=task_result.id,
        job_type=JobType.BULK_SLA_COMPUTATION,
        payload=json.dumps(payload),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def reconcile_payment_analytics(db_session, analytics_client) -> Tuple[bool, List[Dict[str, Any]]]:
    """
    SLA task executing a sliding window audit checkpoint.
    Applies a 15-minute cool-down buffer to clear transient ingestion lag.
    """
    # 1. Establish the consistency time bounds
    end_window = datetime.utcnow() - timedelta(minutes=15)
    start_window = end_window - timedelta(hours=1)
    
    tx_repo = PaymentRepository(db_session)
    analytics_exporter = AnalyticsExporter(analytics_client)
    
    # 2. Extract statistics matrices
    tx_data = {row["status"]: row for row in tx_repo.get_transactional_summary(start_window, end_window)}
    analytics_data = {row["status"]: row for row in analytics_exporter.get_aggregated_analytics_summary(start_window, end_window)}
    
    mismatches = []
    
    # 3. Map comparison metrics
    for status, tx_stats in tx_data.items():
        an_stats = analytics_data.get(status, {"count": 0, "total_amount": 0.0})
        
        count_delta = abs(tx_stats["count"] - an_stats["count"])
        amount_delta = abs(tx_stats["total_amount"] - an_stats["total_amount"])
        
        # Flag structural mismatches instantly
        if count_delta > 0 or amount_delta > 0.01:
            anomaly = {
                "status": status,
                "window": f"{start_window.isoformat()}Z -> {end_window.isoformat()}Z",
                "transactional_truth": tx_stats,
                "analytical_snapshot": an_stats,
                "discrepancy": {"count_drift": count_delta, "amount_drift": amount_delta}
            }
            mismatches.append(anomaly)

    if mismatches:
        logger.critical(f"RECONCILIATION FAULT DETECTED: {mismatches}")
        # trigger_incident_alert_webhook(mismatches)
        return False, mismatches

    logger.info("Reconciliation complete. Data sources are completely synced.")
    return True, []