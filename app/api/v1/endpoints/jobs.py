import json
from typing import List, Optional
from uuid import UUID

from celery.result import AsyncResult
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from fastapi import Request

from app.db.session import get_db
from app.models.job import Job, JobStatus, JobType
from app.services.audit_log import audit_log
from app.services.metrics import increment_counter, timer
from app.tasks.celery_app import celery_app
from app.tasks.sla_tasks import enqueue_sla_computation, enqueue_bulk_sla_computation
from app.utils.correlation import get_correlation_id
from app.utils.logging import get_structured_logger

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
    payload: Optional[dict] = None
    result: Optional[dict] = None
    error: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    created_at: str

    model_config = {"from_attributes": True}


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #

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
        payload=_parse(job.payload),
        result=_parse(job.result),
        error=job.error,
        started_at=job.started_at.isoformat() if job.started_at else None,
        finished_at=job.finished_at.isoformat() if job.finished_at else None,
        created_at=job.created_at.isoformat(),
    )


def _get_job_or_404(db: Session, job_id: UUID) -> Job:
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.")
    return job


def _sync_job_status_from_celery(db: Session, job: Job) -> Job:
    """
    Pull the latest status from Celery backend and update the DB record
    for jobs that haven't reached a terminal state yet.
    """
    if job.status in (JobStatus.SUCCESS, JobStatus.FAILURE, JobStatus.REVOKED):
        return job

    task_result: AsyncResult = AsyncResult(job.celery_task_id, app=celery_app)
    celery_state = task_result.state  # PENDING, STARTED, SUCCESS, FAILURE, REVOKED

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
def submit_sla_computation(payload: SLAJobRequest, request: Request, db: Session = Depends(get_db)):
    """
    Enqueue an async SLA computation job for a single device.
    Returns immediately with a job record for status polling.
    """
    correlation_id = get_correlation_id()
    
    logger.info(
        "Submitting SLA computation job",
        device_id=payload.device_id,
        period=payload.period,
        correlation_id=correlation_id
    )
    
    with timer("job_submission_duration", {"job_type": "sla_computation"}):
        increment_counter("jobs_submitted", tags={"job_type": "sla_computation"})
        job = enqueue_sla_computation(
            db, 
            device_id=payload.device_id, 
            period=payload.period,
            correlation_id=correlation_id
        )
        
        logger.info(
            "SLA computation job submitted",
            job_id=str(job.id),
            celery_task_id=job.celery_task_id,
            correlation_id=correlation_id
        )
        
        return _serialize_job(job)


@router.post(
    "/sla-computation/bulk",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_bulk_sla_computation(payload: BulkSLAJobRequest, request: Request, db: Session = Depends(get_db)):
    """
    Enqueue an async bulk SLA computation job for multiple devices.
    Returns immediately with a job record for status polling.
    """
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
        correlation_id=correlation_id
    )
    
    with timer("job_submission_duration", {"job_type": "bulk_sla_computation"}):
        increment_counter("jobs_submitted", tags={"job_type": "bulk_sla_computation"})
        increment_counter("bulk_job_devices_submitted", value=len(payload.device_ids))
        job = enqueue_bulk_sla_computation(
            db, 
            device_ids=payload.device_ids, 
            period=payload.period,
            correlation_id=correlation_id
        )
        
        logger.info(
            "Bulk SLA computation job submitted",
            job_id=str(job.id),
            celery_task_id=job.celery_task_id,
            device_count=len(payload.device_ids),
            correlation_id=correlation_id
        )
        
        return _serialize_job(job)


@router.get("", response_model=List[JobResponse])
def list_jobs(
    job_type: Optional[JobType] = Query(None),
    status_filter: Optional[JobStatus] = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
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
def get_job(job_id: UUID, db: Session = Depends(get_db)):
    """
    Get a single job's status. Syncs status from Celery backend
    for in-progress jobs before responding.
    """
    job = _get_job_or_404(db, job_id)
    job = _sync_job_status_from_celery(db, job)
    return _serialize_job(job)


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def cancel_job(job_id: UUID, db: Session = Depends(get_db)):
    """
    Revoke a pending or running Celery task and mark the job as REVOKED.
    Does not interrupt already-executing tasks unless terminate=True is set.
    """
    job = _get_job_or_404(db, job_id)
    if job.status in (JobStatus.SUCCESS, JobStatus.FAILURE, JobStatus.REVOKED):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot cancel a job with status '{job.status}'.",
        )

    # Log job revocation before performing the action
    audit_log.log_event(
        db,
        event_type="job_revoked",
        details={
            "job_id": str(job.id),
            "celery_task_id": job.celery_task_id,
            "job_type": job.job_type.value,
            "previous_status": job.status.value,
            "payload": job.payload
        }
    )

    increment_counter("jobs_cancelled", tags={"job_type": job.job_type.value})
    
    celery_app.control.revoke(job.celery_task_id, terminate=False)
    job.status = JobStatus.REVOKED
    db.commit()
