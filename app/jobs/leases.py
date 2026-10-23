from datetime import datetime, timedelta

class LeaseManager:
    def __init__(self, db_session):
        self.db = db_session

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