import logging
from typing import Dict, Any

from celery import Task

from app.tasks.celery_app import celery_app
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)


class IdempotencyCleanupTask(Task):
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
