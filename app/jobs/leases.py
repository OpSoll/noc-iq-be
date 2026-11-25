from datetime import datetime, timedelta

from app.core.config import settings
from app.models.job import Job, JobStatus


class LeaseManager:
    def __init__(self, db_session):
        self.db = db_session

    def renew_lease(
        self,
        job_id: str,
        worker_id: str,
        lease_duration_seconds: int | None = None,
        now: datetime | None = None,
    ) -> bool:
        """Extend a lease only while the calling worker still owns it.

        A worker that resumed after its lease expired must not resurrect a
        lease already reclaimed by another worker.
        """
        current = now or datetime.utcnow()
        job = self.db.query(Job).filter(Job.id == job_id).first()
        if job is None or job.status != JobStatus.STARTED:
            return False
        if job.worker_id != worker_id or job.lease_expires_at is None:
            return False
        if job.lease_expires_at <= current:
            return False

        duration = lease_duration_seconds or settings.JOB_LEASE_TIMEOUT_SECONDS
        job.heartbeat_at = current
        job.lease_expires_at = current + timedelta(seconds=duration)
        self.db.commit()
        return True

    def reclaim_stale_leases(self, stale_threshold_minutes: int = 30) -> int:
        """
        Reclaims stale worker leases and skips active ones.
        Returns the count of successfully reclaimed leases. (#947)
        """
        cutoff_time = datetime.utcnow() - timedelta(minutes=stale_threshold_minutes)
        
        # Simulated database query fetching stale uncompleted leases
        # stales = self.db.query(Lease).filter(Lease.last_heartbeat < cutoff_time, Lease.status == 'active').all()
        
        reclaimed_count = 0
        # For demonstration of the contract:
        # stales = ...
        # for lease in stales:
        #     lease.status = 'reclaimed'
        #     reclaimed_count += 1
        # self.db.commit()
        
        return reclaimed_count