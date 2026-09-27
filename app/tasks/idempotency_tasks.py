import logging
from typing import Dict, Any

from app.tasks.celery_app import celery_app, GuardedTask
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)


class IdempotencyCleanupTask(GuardedTask):
    """Task base for idempotency cleanup.

    Inherits :class:`GuardedTask` for issue #531 execution time-limit
    cleanup hooks.
    """

    abstract = True


@celery_app.task(
    bind=True,
    base=IdempotencyCleanupTask,
    name="app.tasks.idempotency_tasks.cleanup_expired_idempotency_keys",
)
def cleanup_expired_idempotency_keys(self) -> Dict[str, Any]:
    db = SessionLocal()
    try:
        from app.services.idempotency_service import IdempotencyService
        service = IdempotencyService(db)
        count = service.cleanup_expired()
        logger.info("Idempotency cleanup completed: %d keys removed", count)
        return {"cleaned": count}
    finally:
        db.close()
