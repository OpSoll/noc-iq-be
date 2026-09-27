from sqlalchemy.orm import Session
from app.schemas.readiness import ReadinessResponse, DependencyStatus
import time

class ReadinessService:
    def check_dependencies(self, db: Session) -> ReadinessResponse:
        deps = {}
        overall_status = "ok"

        # Check Database
        db_start = time.time()
        try:
            db.execute("SELECT 1")
            db_latency = int((time.time() - db_start) * 1000)
            deps["database"] = DependencyStatus(status="up", latency_ms=db_latency)
        except Exception as e:
            deps["database"] = DependencyStatus(status="down", latency_ms=0, details={"error": str(e)})
            overall_status = "degraded"

        # Check Cache (mocked)
        cache_latency = 5
        deps["redis"] = DependencyStatus(status="up", latency_ms=cache_latency)

        return ReadinessResponse(status=overall_status, dependencies=deps)
