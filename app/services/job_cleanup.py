"""Job retention, cleanup, and lease reclamation service.

BE-042: Provides job retention and cleanup policies to prevent unbounded growth
of job records and maintain database performance.

BE-W5-047: Stale lease reclamation for stuck-task recovery.

BE-W5-052: Retention tiering with configurable windows per job class/status,
investigation/dispute protection, audit-critical preservation, and metrics.
"""
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from sqlalchemy import delete, update
from sqlalchemy.orm import Session

from app.models.job import Job, JobStatus
from app.services.audit_log import audit_log
from app.services.metrics import increment_counter, gauge
from app.utils.logging import get_structured_logger

logger = get_structured_logger("job_cleanup")


# --------------------------------------------------------------------------- #
# BE-W5-048: Per-job-type retry taxonomy defaults                             #
# --------------------------------------------------------------------------- #

RETRY_CLASS_DEFAULTS: Dict[str, Dict[str, object]] = {
    "sla_computation": {
        "retry_class": "exponential_backoff",
        "max_retries": 3,
        "base_delay_seconds": 30,
    },
    "bulk_sla_computation": {
        "retry_class": "exponential_backoff",
        "max_retries": 2,
        "base_delay_seconds": 60,
    },
    "webhook_dispatch": {
        "retry_class": "at_least_once",
        "max_retries": 5,
        "base_delay_seconds": 30,
    },
    "webhook_dr_replay": {
        "retry_class": "at_most_once",
        "max_retries": 1,
        "base_delay_seconds": 60,
    },
}


def get_retry_policy(job_type: str) -> Dict[str, object]:
    """Return the retry taxonomy defaults for *job_type*.

    BE-W5-048: Config-driven; falls back to a sensible default when the job
    type is unknown.
    """
    return RETRY_CLASS_DEFAULTS.get(
        job_type,
        {"retry_class": "at_least_once", "max_retries": 3, "base_delay_seconds": 30},
    )


# --------------------------------------------------------------------------- #
# BE-W5-047: Lease / heartbeat helpers                                        #
# --------------------------------------------------------------------------- #


def heartbeat_job(db: Session, celery_task_id: str, worker_id: str, timeout_seconds: int = 120) -> Optional[Job]:
    """Record a heartbeat for *celery_task_id*.

    Returns the updated Job or None if no matching record exists.
    """
    job = db.query(Job).filter(Job.celery_task_id == celery_task_id).first()
    if not job:
        return None

    now = datetime.utcnow()
    job.heartbeat_at = now
    job.lease_expires_at = now + timedelta(seconds=timeout_seconds)
    job.worker_id = worker_id
    db.commit()
    db.refresh(job)
    return job


def reclaim_stale_leases(
    db: Session,
    timeout_seconds: int = 120,
    batch_size: int = 50,
    dry_run: bool = False,
) -> Dict[str, object]:
    """Find and reclaim jobs whose lease has expired.

    BE-W5-047: Stale leases are reset to PENDING so another worker can pick
    them up.  Single-owner guarantee: only jobs with ``lease_expires_at`` in
    the past AND a non-null ``worker_id`` are candidates.
    """
    now = datetime.utcnow()
    stale = (
        db.query(Job)
        .filter(
            Job.lease_expires_at.isnot(None),
            Job.lease_expires_at < now,
            Job.worker_id.isnot(None),
            Job.status.in_([JobStatus.STARTED]),
        )
        .limit(batch_size)
        .all()
    )

    reclaimed: List[str] = []
    for job in stale:
        prev_worker = job.worker_id
        prev_lease = job.lease_expires_at
        if not dry_run:
            job.worker_id = None
            job.heartbeat_at = None
            job.lease_expires_at = None
            job.status = JobStatus.PENDING
            job.started_at = None
            job.error = (
                f"Lease expired (worker={prev_worker}, "
                f"expired={prev_lease.isoformat() if prev_lease else 'N/A'}); "
                f"reclaimed at {now.isoformat()}"
            )
            # BE-W5-047: reclamation audit event
            try:
                audit_log.log_event(
                    db,
                    event_type="job_lease_reclaimed",
                    details={
                        "job_id": str(job.id),
                        "celery_task_id": job.celery_task_id,
                        "job_type": job.job_type.value,
                        "previous_worker": prev_worker,
                        "lease_expired": prev_lease.isoformat() if prev_lease else None,
                        "reclaimed_at": now.isoformat(),
                    },
                )
            except Exception:
                logger.exception("Failed to write lease-reclamation audit event for job=%s", job.id)
            increment_counter("job_leases_reclaimed", tags={"job_type": job.job_type.value})
        reclaimed.append(str(job.id))

    if not dry_run and reclaimed:
        db.commit()

    logger.info(
        "Lease reclamation scanned %d stale leases; reclaimed=%d dry_run=%s",
        len(stale), len(reclaimed), dry_run,
    )
    return {
        "stale_leases_found": len(stale),
        "reclaimed": len(reclaimed),
        "dry_run": dry_run,
        "checked_at": now.isoformat(),
    }


# --------------------------------------------------------------------------- #
# BE-W5-052: Retention tiering                                                #
# --------------------------------------------------------------------------- #

class JobCleanupService:
    """Manages job retention and cleanup policies with tiering.

    BE-W5-052: Retention windows are configurable by job class and state.
    Records flagged ``under_investigation`` or ``under_dispute`` are never
    removed.  Audit-critical jobs use an extended retention window.
    """

    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------ #
    # Public API                                                          #
    # ------------------------------------------------------------------ #

    def cleanup_old_jobs(
        self,
        *,
        retention_days: Optional[Dict[str, int]] = None,
        dry_run: bool = False,
        batch_size: int = 1000,
    ) -> dict:
        """Clean up old jobs using configurable retention windows.

        The ``retention_days`` dict maps ``JobStatus.value`` → days.  If
        omitted, sensible defaults are used (see :meth:`_default_retention`).

        Jobs flagged ``under_investigation`` or ``under_dispute`` are always
        skipped regardless of age.  Audit-critical jobs use a separate
        extended window.
        """
        windows = retention_days or self._default_retention()
        now = datetime.utcnow()
        total_deleted = 0
        stats: dict = {"dry_run": dry_run, "deleted_by_status": {}}

        for status_value, days in windows.items():
            try:
                status = JobStatus(status_value)
            except ValueError:
                logger.warning("Unknown JobStatus in retention config: %s", status_value)
                continue

            cutoff = now - timedelta(days=days)
            count = self._count_eligible(status, cutoff)
            stats["deleted_by_status"][status_value] = count

            if not dry_run and count > 0:
                deleted = self._delete_eligible(status, cutoff, batch_size)
                total_deleted += deleted
                stats["deleted_by_status"][status_value] = deleted

        stats["total_deleted"] = total_deleted
        stats["cutoffs"] = {
            s: (now - timedelta(days=d)).isoformat() for s, d in windows.items()
        }

        if not dry_run and total_deleted > 0:
            audit_log.log_event(
                self.db,
                event_type="job_cleanup_executed",
                details=stats,
            )
            increment_counter("job_cleanup_deleted", value=total_deleted)

        return stats

    def get_retention_stats(self) -> dict:
        """Get current job retention statistics without deleting anything.

        BE-W5-052: Includes per-status counts, age buckets, and protected
        record counts (investigation / dispute / audit-critical).
        """
        now = datetime.utcnow()
        stats = {
            "total_jobs": self.db.query(Job).count(),
            "by_status": {},
            "by_age": {
                "older_than_30_days": 0,
                "older_than_60_days": 0,
                "older_than_90_days": 0,
                "older_than_180_days": 0,
                "older_than_365_days": 0,
            },
            "protected": {
                "under_investigation": self.db.query(Job)
                .filter(Job.under_investigation.is_(True))
                .count(),
                "under_dispute": self.db.query(Job)
                .filter(Job.under_dispute.is_(True))
                .count(),
                "audit_critical": self.db.query(Job)
                .filter(Job.audit_critical.is_(True))
                .count(),
            },
        }

        for status in JobStatus:
            stats["by_status"][status.value] = (
                self.db.query(Job).filter(Job.status == status).count()
            )

        for label, days in [
            ("older_than_30_days", 30),
            ("older_than_60_days", 60),
            ("older_than_90_days", 90),
            ("older_than_180_days", 180),
            ("older_than_365_days", 365),
        ]:
            cutoff = now - timedelta(days=days)
            stats["by_age"][label] = (
                self.db.query(Job).filter(Job.created_at < cutoff).count()
            )

        return stats

    # ------------------------------------------------------------------ #
    # Helpers                                                             #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _default_retention() -> Dict[str, int]:
        """Return the default retention windows (in days) per status.

        BE-W5-052: Mirrors config defaults. Callers may override.
        """
        from app.core.config import settings as cfg

        return {
            JobStatus.SUCCESS.value: cfg.JOB_RETENTION_SUCCESS_DAYS,
            JobStatus.FAILURE.value: cfg.JOB_RETENTION_FAILURE_DAYS,
            JobStatus.REVOKED.value: cfg.JOB_RETENTION_REVOKED_DAYS,
            JobStatus.QUARANTINED.value: cfg.JOB_RETENTION_QUARANTINED_DAYS,
            JobStatus.DEAD_LETTER.value: cfg.JOB_RETENTION_DEAD_LETTER_DAYS,
        }

    def _build_base_query(self, status: JobStatus, cutoff: datetime):
        """Return a base query for eligible jobs, honouring protection flags.

        BE-W5-052: Jobs under investigation, dispute, or flagged
        audit-critical are never eligible for cleanup via the standard
        retention sweeps.  Audit-critical jobs use an extended window
        cleaned via :meth:`cleanup_audit_critical`.
        """
        q = (
            self.db.query(Job)
            .filter(
                Job.status == status,
                Job.finished_at < cutoff,
                # Protected records are NEVER removed by standard cleanup
                Job.under_investigation.isnot(True),
                Job.under_dispute.isnot(True),
                # Audit-critical jobs always use their own extended window
                Job.audit_critical.isnot(True),
            )
        )
        return q

    def _count_eligible(self, status: JobStatus, cutoff: datetime) -> int:
        return self._build_base_query(status, cutoff).count()

    def _delete_eligible(
        self, status: JobStatus, cutoff: datetime, batch_size: int
    ) -> int:
        total = 0
        while True:
            ids = [
                row[0]
                for row in self._build_base_query(status, cutoff)
                .with_entities(Job.id)
                .limit(batch_size)
                .all()
            ]
            if not ids:
                break
            self.db.execute(delete(Job).where(Job.id.in_(ids)))
            self.db.commit()
            total += len(ids)
            if len(ids) < batch_size:
                break
        return total

    # ------------------------------------------------------------------ #
    # BE-W5-052: Audit-critical cleanup (extended window)                  #
    # ------------------------------------------------------------------ #

    def cleanup_audit_critical(
        self,
        retention_days: int = 365,
        dry_run: bool = False,
        batch_size: int = 500,
    ) -> dict:
        """Clean up audit-critical jobs past their extended window.

        Audit-critical records are preserved longer than regular jobs for
        forensic purposes.  This sweep runs less frequently.
        """
        from app.core.config import settings as cfg

        retention_days = retention_days or cfg.JOB_RETENTION_AUDIT_CRITICAL_DAYS
        cutoff = datetime.utcnow() - timedelta(days=retention_days)

        eligible = (
            self.db.query(Job)
            .filter(
                Job.audit_critical.is_(True),
                Job.finished_at < cutoff,
                Job.under_investigation.isnot(True),
                Job.under_dispute.isnot(True),
            )
        )

        count = eligible.count()
        deleted = 0

        if not dry_run and count > 0:
            while True:
                ids = [
                    row[0]
                    for row in eligible.with_entities(Job.id).limit(batch_size).all()
                ]
                if not ids:
                    break
                self.db.execute(delete(Job).where(Job.id.in_(ids)))
                self.db.commit()
                deleted += len(ids)
                if len(ids) < batch_size:
                    break

        return {
            "audit_critical_eligible": count,
            "audit_critical_deleted": deleted if not dry_run else count,
            "dry_run": dry_run,
            "cutoff": cutoff.isoformat(),
            "retention_days": retention_days,
        }

    # ------------------------------------------------------------------ #
    # BE-W5-052: Protection flag management                               #
    # ------------------------------------------------------------------ #

    def set_investigation_flag(self, job_id: str, under_investigation: bool = True) -> Optional[Job]:
        """Toggle the ``under_investigation`` flag for a job.

        Returns the updated Job or None if not found.
        """
        job = self.db.query(Job).filter(Job.id == job_id).first()
        if not job:
            return None
        job.under_investigation = under_investigation
        self.db.commit()
        self.db.refresh(job)

        audit_log.log_event(
            self.db,
            event_type="job_investigation_flag_changed",
            details={
                "job_id": job_id,
                "under_investigation": under_investigation,
            },
        )
        return job

    def set_dispute_flag(self, job_id: str, under_dispute: bool = True) -> Optional[Job]:
        """Toggle the ``under_dispute`` flag for a job.

        Returns the updated Job or None if not found.
        """
        job = self.db.query(Job).filter(Job.id == job_id).first()
        if not job:
            return None
        job.under_dispute = under_dispute
        self.db.commit()
        self.db.refresh(job)

        audit_log.log_event(
            self.db,
            event_type="job_dispute_flag_changed",
            details={
                "job_id": job_id,
                "under_dispute": under_dispute,
            },
        )
        return job
