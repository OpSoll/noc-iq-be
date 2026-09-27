"""Periodic guard that revokes hung Celery tasks.

Issue #531: long-running tasks (e.g. stalled Stellar RPC network fetches)
can hang workers indefinitely. The execution time limits configured in
:mod:`app.tasks.celery_app` kill individual executions; this module sweeps
jobs that are still marked STARTED after their lease (time limit) expired
and revokes them so they cannot pin workers forever.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.job import Job, JobStatus
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.timeout_guard.revoke_hung_tasks")
def revoke_hung_tasks() -> Dict[str, Any]:
    """Revoke jobs still STARTED past their lease expiry and mark REVOKED.

    Runs from the beat schedule every 60 s. Gated by
    ``JOB_LEASE_RECLAMATION_ENABLED``; ``JOB_LEASE_TIMEOUT_SECONDS`` defines
    how long a task may run before it is considered hung.
    """
    if not settings.JOB_LEASE_RECLAMATION_ENABLED:
        return {"scanned": 0, "revoked": 0, "disabled": True}

    now = datetime.utcnow()
    lease_cutoff = now - timedelta(seconds=settings.JOB_LEASE_TIMEOUT_SECONDS)

    db = SessionLocal()
    try:
        hung = (
            db.query(Job)
            .filter(
                Job.status == JobStatus.STARTED,
                Job.lease_expires_at.isnot(None),
                Job.lease_expires_at < lease_cutoff,
            )
            .limit(settings.JOB_LEASE_RECLAMATION_BATCH_SIZE)
            .all()
        )

        revoked: list[str] = []
        for job in hung:
            # Only revoke when the task has been running well past the time
            # limit; terminate the worker-side execution if reachable.
            if not settings.CELERY_TASK_ALWAYS_EAGER:
                try:
                    celery_app.control.revoke(
                        job.celery_task_id,
                        terminate=True,
                        signal="SIGKILL",
                    )
                except Exception:
                    logger.exception(
                        "Failed to revoke hung task %s (job %s)",
                        job.celery_task_id,
                        job.id,
                    )
            job.status = JobStatus.REVOKED
            job.error = (
                "Revoked by timeout guard: task exceeded execution time "
                "limit and was considered hung"
            )
            job.error_code = "TASK_TIMEOUT_REVOKED"
            job.error_retryable = False
            job.finished_at = now
            job.heartbeat_at = None
            job.lease_expires_at = None
            revoked.append(str(job.id))
            logger.warning(
                "Timeout guard revoked hung job %s (celery task %s)",
                job.id,
                job.celery_task_id,
            )

        if revoked:
            db.commit()

        return {
            "scanned": len(hung),
            "revoked": len(revoked),
            "checked_at": now.isoformat(),
            "lease_timeout_seconds": settings.JOB_LEASE_TIMEOUT_SECONDS,
        }
    finally:
        db.close()
