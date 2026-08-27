import json
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from celery.result import AsyncResult
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from fastapi import Request

from app.db.session import get_db
from app.models.job import (
    Job,
    JobErrorDetail,
    JobResultEnvelope,
    JobStatus,
    JobType,
    RetryClass,
)
from app.services.audit_log import audit_log
from app.services.job_cleanup import (
    JobCleanupService,
    get_retry_policy,
    heartbeat_job,
    reclaim_stale_leases,
)
from app.services.metrics import increment_counter, timer
from app.tasks.celery_app import celery_app
from app.tasks.sla_tasks import enqueue_sla_computation, enqueue_bulk_sla_computation
from app.tasks.webhook_tasks import dispatch_webhook_delivery
from app.utils.correlation import get_correlation_id
from app.utils.logging import get_structured_logger
from app.core.security import require_engineer, require_admin
from app.core.config import settings as cfg

logger = get_structured_logger("jobs_api")

router = APIRouter(prefix="/jobs", tags=["Jobs"])


# --------------------------------------------------------------------------- #
# Schemas                                                                      #
# --------------------------------------------------------------------------- #

class SLAJobRequest(BaseModel):
    device_id: str
    period: str  # e.g. "2024-01", "2024-Q1"


class BulkSLAJobRequest(BaseModel):
    device_ids: List[str]
    period: str


class JobResponse(BaseModel):
    id: UUID
    celery_task_id: str
    job_type: JobType
    status: JobStatus
    progress: float
    progress_details: Optional[dict] = None
    partial_results: Optional[dict] = None
    per_item_errors: Optional[dict] = None
    payload: Optional[dict] = None
    result: Optional[dict] = None
    error: Optional[str] = None
    # BE-W5-050: Typed error detail
    error_detail: Optional[JobErrorDetail] = None
    # BE-041: Retry metadata
    retry_count: int = 0
    max_retries: int = 3
    retry_class: Optional[str] = None
    last_retried_at: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    created_at: str
    # BE-W5-054: Quarantine metadata
    payload_hash: Optional[str] = None
    quarantine_reason: Optional[str] = None
    quarantined_at: Optional[str] = None
    # BE-W5-048: Dead-letter metadata
    dead_letter_reason: Optional[str] = None
    dead_letter_at: Optional[str] = None
    # BE-W5-047: Lease heartbeat
    worker_id: Optional[str] = None
    heartbeat_at: Optional[str] = None
    lease_expires_at: Optional[str] = None
    # BE-W5-052: Protection flags
    under_investigation: bool = False
    under_dispute: bool = False
    audit_critical: bool = False

    model_config = {"from_attributes": True}


# BE-W5-050: Standardised result envelope response
class JobEnvelopeResponse(BaseModel):
    """Standardised envelope wrapping every job endpoint response."""
    job_id: str
    celery_task_id: str
    job_type: str
    status: str
    progress: float = 0.0
    result: Optional[Dict[str, Any]] = None
    error: Optional[JobErrorDetail] = None
    retry_count: int = 0
    max_retries: int = 3
    retry_class: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    created_at: Optional[str] = None
    # Extended metadata
    worker_id: Optional[str] = None
    lease_expires_at: Optional[str] = None
    under_investigation: bool = False
    under_dispute: bool = False
    audit_critical: bool = False

    model_config = {"from_attributes": True}


# BE-042: Job cleanup schemas (extended for BE-W5-052)
class JobRetentionStatsResponse(BaseModel):
    """Current job retention statistics."""
    total_jobs: int
    by_status: dict
    by_age: dict
    protected: Optional[dict] = None


class JobCleanupRequest(BaseModel):
    """Request parameters for job cleanup."""
    retention_days: Optional[Dict[str, int]] = None  # BE-W5-052: per-status windows
    dry_run: bool = False
    batch_size: int = 1000


class JobCleanupResponse(BaseModel):
    """Response from job cleanup operation."""
    total_deleted: int
    deleted_by_status: dict
    cutoffs: dict
    dry_run: bool


# BE-W5-047: Lease reclamation
class LeaseReclamationRequest(BaseModel):
    timeout_seconds: int = 120
    batch_size: int = 50
    dry_run: bool = False


class LeaseReclamationResponse(BaseModel):
    stale_leases_found: int
    reclaimed: int
    dry_run: bool
    checked_at: str


# BE-W5-052: Audit-critical cleanup
class AuditCriticalCleanupResponse(BaseModel):
    audit_critical_eligible: int
    audit_critical_deleted: int
    dry_run: bool
    cutoff: str
    retention_days: int


# BE-W5-052: Protection flag management
class ProtectionFlagRequest(BaseModel):
    under_investigation: Optional[bool] = None
    under_dispute: Optional[bool] = None


# BE-W5-048: Retry taxonomy response
class RetryPolicyResponse(BaseModel):
    job_type: str
    retry_class: str
    max_retries: int
    base_delay_seconds: int


class JobHeartbeatResponse(BaseModel):
    """Response confirming a lease heartbeat was recorded (BE-W5-047)."""
    job_id: str
    heartbeat_at: str


class DeadLetterSummary(BaseModel):
    id: UUID
    celery_task_id: str
    job_type: JobType
    dead_letter_reason: Optional[str] = None
    dead_letter_at: Optional[str] = None
    retry_count: int
    max_retries: int
    created_at: str


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #


def _build_error_detail(job: Job) -> Optional[JobErrorDetail]:
    """Build a typed error detail from a Job record for BE-W5-050."""
    if job.error_code:
        return JobErrorDetail(
            code=job.error_code,
            message=job.error or "",
            retryable=job.error_retryable if job.error_retryable is not None else False,
            details=job.error_details,
        )
    if job.error:
        return JobErrorDetail(
            code="UNKNOWN",
            message=job.error,
            retryable=False,
        )
    return None


def _serialize_job(job: Job) -> JobResponse:
    def _parse(val):
        if val is None:
            return None
        try:
            return json.loads(val)
        except (json.JSONDecodeError, TypeError):
            return val

    return JobResponse(
        id=job.id,
        celery_task_id=job.celery_task_id,
        job_type=job.job_type,
        status=job.status,
        progress=job.progress,
        progress_details=job.progress_details,
        partial_results=job.partial_results,
        per_item_errors=job.per_item_errors,
        payload=_parse(job.payload),
        result=_parse(job.result),
        error=job.error,
        error_detail=_build_error_detail(job),
        retry_count=job.retry_count,
        max_retries=job.max_retries,
        retry_class=job.retry_class,
        last_retried_at=job.last_retried_at.isoformat() if job.last_retried_at else None,
        started_at=job.started_at.isoformat() if job.started_at else None,
        finished_at=job.finished_at.isoformat() if job.finished_at else None,
        created_at=job.created_at.isoformat(),
        payload_hash=job.payload_hash,
        quarantine_reason=job.quarantine_reason,
        quarantined_at=job.quarantined_at.isoformat() if job.quarantined_at else None,
        dead_letter_reason=job.dead_letter_reason,
        dead_letter_at=job.dead_letter_at.isoformat() if job.dead_letter_at else None,
        worker_id=job.worker_id,
        heartbeat_at=job.heartbeat_at.isoformat() if job.heartbeat_at else None,
        lease_expires_at=job.lease_expires_at.isoformat() if job.lease_expires_at else None,
        under_investigation=job.under_investigation or False,
        under_dispute=job.under_dispute or False,
        audit_critical=job.audit_critical or False,
    )


def _serialize_envelope(job: Job) -> JobEnvelopeResponse:
    """BE-W5-050: Serialize a Job as the standardised result envelope."""
    return JobEnvelopeResponse(
        job_id=str(job.id),
        celery_task_id=job.celery_task_id,
        job_type=job.job_type.value,
        status=job.status.value,
        progress=job.progress or 0.0,
        result=json.loads(job.result) if job.result else None,
        error=_build_error_detail(job),
        retry_count=job.retry_count or 0,
        max_retries=job.max_retries or 3,
        retry_class=job.retry_class,
        started_at=job.started_at.isoformat() if job.started_at else None,
        finished_at=job.finished_at.isoformat() if job.finished_at else None,
        created_at=job.created_at.isoformat(),
        worker_id=job.worker_id,
        lease_expires_at=job.lease_expires_at.isoformat() if job.lease_expires_at else None,
        under_investigation=job.under_investigation or False,
        under_dispute=job.under_dispute or False,
        audit_critical=job.audit_critical or False,
    )


def _get_job_or_404(db: Session, job_id: UUID) -> Job:
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.")
    return job


def _sync_job_status_from_celery(db: Session, job: Job) -> Job:
    """Pull latest status from Celery backend for in-progress jobs."""
    if job.status in (JobStatus.SUCCESS, JobStatus.FAILURE, JobStatus.REVOKED,
                       JobStatus.DEAD_LETTER, JobStatus.QUARANTINED):
        return job

    task_result: AsyncResult = AsyncResult(job.celery_task_id, app=celery_app)
    celery_state = task_result.state

    state_map = {
        "PENDING": JobStatus.PENDING,
        "STARTED": JobStatus.STARTED,
        "SUCCESS": JobStatus.SUCCESS,
        "FAILURE": JobStatus.FAILURE,
        "REVOKED": JobStatus.REVOKED,
    }

    new_status = state_map.get(celery_state, job.status)
    if new_status != job.status:
        job.status = new_status
        db.commit()
        db.refresh(job)

    return job


# --------------------------------------------------------------------------- #
# Endpoints                                                                    #
# --------------------------------------------------------------------------- #

@router.post(
    "/sla-computation",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_sla_computation(
    payload: SLAJobRequest,
    request: Request,
    current_user=Depends(require_engineer),
    db: Session = Depends(get_db),
):
    """Enqueue an async SLA computation job for a single device."""
    correlation_id = get_correlation_id()

    logger.info(
        "Submitting SLA computation job",
        device_id=payload.device_id,
        period=payload.period,
        correlation_id=correlation_id,
    )

    with timer("job_submission_duration", {"job_type": "sla_computation"}):
        increment_counter("jobs_submitted", tags={"job_type": "sla_computation"})
        job = enqueue_sla_computation(
            db,
            device_id=payload.device_id,
            period=payload.period,
            correlation_id=correlation_id,
        )

        # BE-W5-048: Apply retry taxonomy defaults
        policy = get_retry_policy(JobType.SLA_COMPUTATION.value)
        job.retry_class = str(policy.get("retry_class", "exponential_backoff"))
        db.commit()
        db.refresh(job)

        logger.info(
            "SLA computation job submitted",
            job_id=str(job.id),
            celery_task_id=job.celery_task_id,
            correlation_id=correlation_id,
        )
        return _serialize_job(job)


@router.post(
    "/sla-computation/bulk",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_bulk_sla_computation(
    payload: BulkSLAJobRequest,
    request: Request,
    current_user=Depends(require_engineer),
    db: Session = Depends(get_db),
):
    """Enqueue an async bulk SLA computation job for multiple devices."""
    correlation_id = get_correlation_id()

    if not payload.device_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="device_ids must not be empty.",
        )

    logger.info(
        "Submitting bulk SLA computation job",
        device_count=len(payload.device_ids),
        period=payload.period,
        correlation_id=correlation_id,
    )

    with timer("job_submission_duration", {"job_type": "bulk_sla_computation"}):
        increment_counter("jobs_submitted", tags={"job_type": "bulk_sla_computation"})
        increment_counter("bulk_job_devices_submitted", value=len(payload.device_ids))
        job = enqueue_bulk_sla_computation(
            db,
            device_ids=payload.device_ids,
            period=payload.period,
            correlation_id=correlation_id,
        )

        policy = get_retry_policy(JobType.BULK_SLA_COMPUTATION.value)
        job.retry_class = str(policy.get("retry_class", "exponential_backoff"))
        db.commit()
        db.refresh(job)

        logger.info(
            "Bulk SLA computation job submitted",
            job_id=str(job.id),
            celery_task_id=job.celery_task_id,
            device_count=len(payload.device_ids),
            correlation_id=correlation_id,
        )
        return _serialize_job(job)


@router.get("", response_model=List[JobResponse])
def list_jobs(
    job_type: Optional[JobType] = Query(None),
    status_filter: Optional[JobStatus] = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    current_user=Depends(require_engineer),
    db: Session = Depends(get_db),
):
    """List all jobs with optional filters."""
    query = db.query(Job).order_by(Job.created_at.desc())
    if job_type is not None:
        query = query.filter(Job.job_type == job_type)
    if status_filter is not None:
        query = query.filter(Job.status == status_filter)
    return [_serialize_job(j) for j in query.limit(limit).all()]


@router.get("/{job_id}", response_model=JobResponse)
def get_job(
    job_id: UUID,
    current_user=Depends(require_engineer),
    db: Session = Depends(get_db),
):
    """Get a single job's status. Syncs status from Celery for in-progress jobs."""
    job = _get_job_or_404(db, job_id)
    job = _sync_job_status_from_celery(db, job)
    return _serialize_job(job)


# BE-W5-050: Standardised envelope endpoint
@router.get("/{job_id}/envelope", response_model=JobEnvelopeResponse)
def get_job_envelope(
    job_id: UUID,
    current_user=Depends(require_engineer),
    db: Session = Depends(get_db),
):
    """Get a single job wrapped in the standardised result envelope (BE-W5-050)."""
    job = _get_job_or_404(db, job_id)
    job = _sync_job_status_from_celery(db, job)
    return _serialize_envelope(job)


class JobProgressResponse(BaseModel):
    id: UUID
    status: JobStatus
    progress: float
    progress_details: Optional[dict] = None
    partial_results: Optional[dict] = None
    per_item_errors: Optional[dict] = None

    model_config = {"from_attributes": True}


@router.get("/{job_id}/progress", response_model=JobProgressResponse)
def get_job_progress(
    job_id: UUID,
    current_user=Depends(require_engineer),
    db: Session = Depends(get_db),
):
    """Lightweight polling endpoint returning only progress fields."""
    job = _get_job_or_404(db, job_id)
    job = _sync_job_status_from_celery(db, job)
    return JobProgressResponse(
        id=job.id,
        status=job.status,
        progress=job.progress,
        progress_details=job.progress_details,
        partial_results=job.partial_results,
        per_item_errors=job.per_item_errors,
    )


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def cancel_job(
    job_id: UUID,
    current_user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Revoke a pending or running Celery task and mark the job as REVOKED."""
    job = _get_job_or_404(db, job_id)
    if job.status in (JobStatus.SUCCESS, JobStatus.FAILURE, JobStatus.REVOKED,
                       JobStatus.DEAD_LETTER):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot cancel a job with status '{job.status}'.",
        )

    audit_log.log_event(
        db,
        event_type="job_revoked",
        details={
            "job_id": str(job.id),
            "celery_task_id": job.celery_task_id,
            "job_type": job.job_type.value,
            "previous_status": job.status.value,
            "payload": job.payload,
        },
    )

    increment_counter("jobs_cancelled", tags={"job_type": job.job_type.value})
    celery_app.control.revoke(job.celery_task_id, terminate=False)
    job.status = JobStatus.REVOKED
    db.commit()


# --------------------------------------------------------------------------- #
# BE-041 / BE-W5-048: Job retry with taxonomy governance                       #
# --------------------------------------------------------------------------- #

class JobRetryResponse(BaseModel):
    id: UUID
    celery_task_id: str
    job_type: JobType
    status: JobStatus
    retry_count: int
    max_retries: int
    retry_class: Optional[str] = None
    message: str

    model_config = {"from_attributes": True}


@router.post(
    "/{job_id}/retry",
    response_model=JobRetryResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def retry_job(
    job_id: UUID,
    request: Request,
    current_user=Depends(require_engineer),
    db: Session = Depends(get_db),
):
    """Retry a failed, revoked, or dead-letter job.

    BE-W5-048: Retry taxonomy governance — retries honour the configured
    ``retry_class`` and ``max_retries``.  Exhausted dead-letter jobs can be
    retried manually by operators.
    """
    correlation_id = get_correlation_id()
    job = _get_job_or_404(db, job_id)

    retryable_statuses = (JobStatus.FAILURE, JobStatus.REVOKED, JobStatus.DEAD_LETTER)
    if job.status not in retryable_statuses:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Cannot retry job with status '{job.status.value}'. "
                f"Only FAILED, REVOKED, or DEAD_LETTER jobs can be retried."
            ),
        )

    if job.retry_count >= job.max_retries:
        # BE-W5-048: If dead-letter is enabled, move exhausted jobs there
        if cfg.JOB_RETRY_DEAD_LETTER_ENABLED and job.status != JobStatus.DEAD_LETTER:
            job.status = JobStatus.DEAD_LETTER
            job.dead_letter_reason = (
                f"Max retries exhausted ({job.max_retries}). "
                f"Last error: {job.error}"
            )
            job.dead_letter_at = datetime.utcnow()
            job.finished_at = datetime.utcnow()
            db.commit()
            db.refresh(job)
            increment_counter("jobs_dead_lettered", tags={"job_type": job.job_type.value})
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Job has exceeded maximum retry limit ({job.max_retries}) "
                    f"and has been moved to DEAD_LETTER. Current retry count: {job.retry_count}"
                ),
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Job has exceeded maximum retry limit ({job.max_retries}). "
                f"Current retry count: {job.retry_count}"
            ),
        )

    # BE-W5-048: Check retry class — at_most_once jobs cannot be auto-retried
    if job.retry_class == RetryClass.AT_MOST_ONCE.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Job has retry class 'at_most_once' and cannot be retried.",
        )

    audit_log.log_event(
        db,
        event_type="job_retry_initiated",
        details={
            "job_id": str(job.id),
            "original_celery_task_id": job.celery_task_id,
            "job_type": job.job_type.value,
            "previous_status": job.status.value,
            "retry_count": job.retry_count + 1,
            "max_retries": job.max_retries,
            "retry_class": job.retry_class,
            "payload": job.payload,
            "previous_error": job.error,
            "correlation_id": correlation_id,
            "initiated_by": getattr(current_user, "email", "unknown"),
        },
    )

    logger.info(
        "Retrying job",
        job_id=str(job.id),
        job_type=job.job_type.value,
        retry_count=job.retry_count + 1,
        max_retries=job.max_retries,
        retry_class=job.retry_class,
        correlation_id=correlation_id,
    )

    job.retry_count += 1
    job.last_retried_at = datetime.utcnow()
    job.error = None
    job.error_code = None
    job.error_retryable = None
    job.error_details = None
    job.status = JobStatus.PENDING
    job.progress = 0.0
    job.started_at = None
    job.finished_at = None
    job.dead_letter_reason = None
    job.dead_letter_at = None

    try:
        payload = json.loads(job.payload) if job.payload else {}

        if job.job_type == JobType.SLA_COMPUTATION:
            new_task = enqueue_sla_computation(
                db,
                device_id=payload.get("device_id", ""),
                period=payload.get("period", ""),
                correlation_id=correlation_id,
            )
        elif job.job_type == JobType.BULK_SLA_COMPUTATION:
            new_task = enqueue_bulk_sla_computation(
                db,
                device_ids=payload.get("device_ids", []),
                period=payload.get("period", ""),
                correlation_id=correlation_id,
            )
        elif job.job_type == JobType.WEBHOOK_DISPATCH:
            from app.tasks.webhook_tasks import dispatch_webhook_delivery

            task_result = dispatch_webhook_delivery.delay(payload)
            job.celery_task_id = task_result.id
            db.commit()
            db.refresh(job)

            return JobRetryResponse(
                id=job.id,
                celery_task_id=job.celery_task_id,
                job_type=job.job_type,
                status=job.status,
                retry_count=job.retry_count,
                max_retries=job.max_retries,
                retry_class=job.retry_class,
                message=f"Job retry #{job.retry_count} initiated successfully",
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported job type for retry: {job.job_type.value}",
            )

        job.celery_task_id = new_task.celery_task_id
        db.commit()
        db.refresh(job)

        logger.info(
            "Job retry enqueued successfully",
            job_id=str(job.id),
            new_celery_task_id=job.celery_task_id,
            retry_count=job.retry_count,
            correlation_id=correlation_id,
        )

        return JobRetryResponse(
            id=job.id,
            celery_task_id=job.celery_task_id,
            job_type=job.job_type,
            status=job.status,
            retry_count=job.retry_count,
            max_retries=job.max_retries,
            retry_class=job.retry_class,
            message=f"Job retry #{job.retry_count} initiated successfully",
        )

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(
            "Failed to retry job",
            job_id=str(job.id),
            error=str(e),
            correlation_id=correlation_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retry job: {str(e)}",
        )


# --------------------------------------------------------------------------- #
# BE-W5-048: Retry taxonomy endpoints                                          #
# --------------------------------------------------------------------------- #

@router.get("/retry-policies", response_model=List[RetryPolicyResponse])
def list_retry_policies(
    current_user=Depends(require_engineer),
):
    """List configured retry taxonomy policies per job type (BE-W5-048)."""
    from app.services.job_cleanup import RETRY_CLASS_DEFAULTS
    return [
        RetryPolicyResponse(
            job_type=jt,
            retry_class=str(p["retry_class"]),
            max_retries=int(p["max_retries"]),
            base_delay_seconds=int(p["base_delay_seconds"]),
        )
        for jt, p in sorted(RETRY_CLASS_DEFAULTS.items())
    ]


# BE-W5-048: Dead-letter listing
@router.get("/dead-letter", response_model=List[DeadLetterSummary])
def list_dead_letter_jobs(
    limit: int = Query(50, ge=1, le=200),
    current_user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    """List jobs in DEAD_LETTER status (BE-W5-048)."""
    rows = (
        db.query(Job)
        .filter(Job.status == JobStatus.DEAD_LETTER)
        .order_by(Job.dead_letter_at.desc().nullslast())
        .limit(limit)
        .all()
    )
    return [
        DeadLetterSummary(
            id=j.id,
            celery_task_id=j.celery_task_id,
            job_type=j.job_type,
            dead_letter_reason=j.dead_letter_reason,
            dead_letter_at=j.dead_letter_at.isoformat() if j.dead_letter_at else None,
            retry_count=j.retry_count,
            max_retries=j.max_retries,
            created_at=j.created_at.isoformat(),
        )
        for j in rows
    ]


# --------------------------------------------------------------------------- #
# BE-W5-047: Lease heartbeat / reclamation endpoints                           #
# --------------------------------------------------------------------------- #

@router.post("/{job_id}/heartbeat", response_model=JobHeartbeatResponse, status_code=status.HTTP_200_OK)
def record_job_heartbeat(
    job_id: UUID,
    worker_id: str = Query(..., description="Worker identifier"),
    current_user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Record a lease heartbeat for a running job (BE-W5-047).

    Internal endpoint used by workers to extend their lease on a job.
    """
    job = _get_job_or_404(db, job_id)
    if job.status not in (JobStatus.STARTED, JobStatus.PENDING):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot heartbeat a job with status '{job.status.value}'.",
        )

    updated = heartbeat_job(
        db,
        job.celery_task_id,
        worker_id,
        timeout_seconds=cfg.JOB_LEASE_TIMEOUT_SECONDS,
    )
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update heartbeat.",
        )
    return {"job_id": str(job_id), "heartbeat_at": updated.heartbeat_at.isoformat()}


@router.post("/reclaim-stale-leases", response_model=LeaseReclamationResponse)
def reclaim_stale_job_leases(
    payload: LeaseReclamationRequest,
    current_user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Reclaim jobs with expired worker leases (BE-W5-047).

    Stale leases are returned to PENDING so another worker can pick them up.
    """
    if not cfg.JOB_LEASE_RECLAMATION_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Lease reclamation is disabled in configuration.",
        )

    result = reclaim_stale_leases(
        db,
        timeout_seconds=payload.timeout_seconds,
        batch_size=payload.batch_size,
        dry_run=payload.dry_run,
    )
    return LeaseReclamationResponse(**result)


# --------------------------------------------------------------------------- #
# BE-W5-052: Retention tiering endpoints                                       #
# --------------------------------------------------------------------------- #

@router.get("/retention-stats", response_model=JobRetentionStatsResponse)
def get_job_retention_stats(
    current_user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Get current job retention statistics including protection flags (BE-W5-052)."""
    cleanup_service = JobCleanupService(db)
    return cleanup_service.get_retention_stats()


@router.post("/cleanup", response_model=JobCleanupResponse)
def cleanup_old_jobs(
    payload: JobCleanupRequest,
    current_user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Clean up old jobs based on retention tiering policy (BE-W5-052).

    Protected records (under investigation, under dispute, audit-critical)
    are excluded from standard cleanup sweeps.
    """
    cleanup_service = JobCleanupService(db)

    result = cleanup_service.cleanup_old_jobs(
        retention_days=payload.retention_days,
        dry_run=payload.dry_run,
        batch_size=payload.batch_size,
    )

    audit_log.log_event(
        db,
        event_type="job_cleanup_executed",
        details={
            "total_deleted": result["total_deleted"],
            "deleted_by_status": result["deleted_by_status"],
            "dry_run": payload.dry_run,
            "executed_by": getattr(current_user, "email", "unknown"),
        },
    )

    return JobCleanupResponse(**result)


@router.post("/cleanup-audit-critical", response_model=AuditCriticalCleanupResponse)
def cleanup_audit_critical_jobs(
    retention_days: int = 365,
    dry_run: bool = False,
    current_user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Clean up audit-critical jobs past their extended retention window (BE-W5-052)."""
    cleanup_service = JobCleanupService(db)
    result = cleanup_service.cleanup_audit_critical(
        retention_days=retention_days,
        dry_run=dry_run,
    )
    return AuditCriticalCleanupResponse(**result)


# BE-W5-052: Protection flag management
@router.patch("/{job_id}/protection-flags", response_model=JobResponse)
def update_job_protection_flags(
    job_id: UUID,
    payload: ProtectionFlagRequest,
    current_user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Set investigation or dispute flags on a job (BE-W5-052).

    Flagged records are protected from all automatic cleanup sweeps.
    """
    job = _get_job_or_404(db, job_id)

    if payload.under_investigation is not None:
        job.under_investigation = payload.under_investigation
        audit_log.log_event(
            db,
            event_type="job_investigation_flag_changed",
            details={
                "job_id": str(job.id),
                "under_investigation": payload.under_investigation,
                "updated_by": getattr(current_user, "email", "unknown"),
            },
        )

    if payload.under_dispute is not None:
        job.under_dispute = payload.under_dispute
        audit_log.log_event(
            db,
            event_type="job_dispute_flag_changed",
            details={
                "job_id": str(job.id),
                "under_dispute": payload.under_dispute,
                "updated_by": getattr(current_user, "email", "unknown"),
            },
        )

    db.commit()
    db.refresh(job)
    return _serialize_job(job)


# --------------------------------------------------------------------------- #
# BE-W5-054: Poison-message quarantine endpoints                               #
# --------------------------------------------------------------------------- #

class QuarantinedJobSummary(BaseModel):
    id: UUID
    celery_task_id: str
    job_type: JobType
    payload_hash: str
    quarantine_reason: Optional[str] = None
    quarantined_at: str
    retry_count: int
    max_retries: int


class JobReleaseResponse(BaseModel):
    job_id: UUID
    celery_task_id: str
    job_type: JobType
    status: JobStatus
    retry_count: int
    max_retries: int
    message: str


@router.get("/quarantined", response_model=List[QuarantinedJobSummary])
def list_quarantined_jobs(
    limit: int = Query(50, ge=1, le=200),
    current_user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    """List quarantined jobs (BE-W5-054)."""
    rows = (
        db.query(Job)
        .filter(Job.status == JobStatus.QUARANTINED)
        .order_by(Job.quarantined_at.desc().nullslast())
        .limit(limit)
        .all()
    )
    return [
        QuarantinedJobSummary(
            id=j.id,
            celery_task_id=j.celery_task_id,
            job_type=j.job_type,
            payload_hash=j.payload_hash or "",
            quarantine_reason=j.quarantine_reason,
            quarantined_at=j.quarantined_at.isoformat() if j.quarantined_at else "",
            retry_count=j.retry_count,
            max_retries=j.max_retries,
        )
        for j in rows
    ]


@router.post(
    "/{job_id}/release-from-quarantine",
    response_model=JobReleaseResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def release_job_from_quarantine(
    job_id: UUID,
    request: Request,
    current_user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Release a quarantined job back into the retry pipeline (BE-W5-054)."""
    correlation_id = get_correlation_id()
    job = _get_job_or_404(db, job_id)

    if job.status != JobStatus.QUARANTINED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Job is not quarantined (current status: {job.status.value}).",
        )

    audit_log.log_event(
        db,
        event_type="job_released_from_quarantine",
        details={
            "job_id": str(job.id),
            "celery_task_id": job.celery_task_id,
            "job_type": job.job_type.value,
            "payload_hash": job.payload_hash,
            "previous_quarantine_reason": job.quarantine_reason,
            "released_by": getattr(current_user, "email", "unknown"),
            "correlation_id": correlation_id,
        },
    )

    job.quarantined_at = None
    job.quarantine_reason = None
    job.payload_hash = None
    job.retry_count = 0
    job.error = None
    job.error_code = None
    job.error_retryable = None
    job.error_details = None
    job.finished_at = None
    job.started_at = None
    job.progress = 0.0
    job.status = JobStatus.PENDING

    try:
        payload = json.loads(job.payload) if job.payload else {}

        if job.job_type == JobType.SLA_COMPUTATION:
            new_task = enqueue_sla_computation(
                db,
                device_id=payload.get("device_id", ""),
                period=payload.get("period", ""),
                correlation_id=correlation_id,
            )
        elif job.job_type == JobType.BULK_SLA_COMPUTATION:
            new_task = enqueue_bulk_sla_computation(
                db,
                device_ids=payload.get("device_ids", []),
                period=payload.get("period", ""),
                correlation_id=correlation_id,
            )
        elif job.job_type == JobType.WEBHOOK_DISPATCH:
            from app.tasks.webhook_tasks import dispatch_webhook_delivery

            delivery_id = payload.get("delivery_id") or payload.get("idempotency_key")
            if not delivery_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="webhook dispatch job payload missing delivery_id.",
                )
            task_result = dispatch_webhook_delivery.delay(delivery_id)
            job.celery_task_id = task_result.id
            db.commit()
            db.refresh(job)
            return JobReleaseResponse(
                job_id=job.id,
                celery_task_id=job.celery_task_id,
                job_type=job.job_type,
                status=job.status,
                retry_count=job.retry_count,
                max_retries=job.max_retries,
                message="Released from quarantine and re-dispatched.",
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported job type for release: {job.job_type.value}",
            )

        job.celery_task_id = new_task.celery_task_id
        db.commit()
        db.refresh(job)
        return JobReleaseResponse(
            job_id=job.id,
            celery_task_id=job.celery_task_id,
            job_type=job.job_type,
            status=job.status,
            retry_count=job.retry_count,
            max_retries=job.max_retries,
            message="Released from quarantine and re-enqueued.",
        )
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        logger.error(
            "Failed to release quarantine job=%s: %s", job_id, exc,
            correlation_id=correlation_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to release quarantine: {str(exc)}",
        )
