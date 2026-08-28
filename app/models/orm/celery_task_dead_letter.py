from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Column, DateTime, Index, String, Text

from app.db.base import Base


class CeleryTaskDeadLetterORM(Base):
    """Audit row for a Celery task that permanently failed.

    Issue #530: unhandled worker task exceptions are routed to the
    ``celery_dead_letter`` queue and their payload plus traceback is stored
    here so operators can inspect, replay, or alert on them.
    """

    __tablename__ = "celery_task_dead_letters"

    id = Column(String(255), primary_key=True, default=lambda: str(uuid4()))
    task_id = Column(String(255), nullable=False, index=True)
    task_name = Column(String(255), nullable=False)
    queue = Column(String(64), nullable=False, default="celery_dead_letter")
    args_json = Column(Text, nullable=True)
    kwargs_json = Column(Text, nullable=True)
    exception = Column(Text, nullable=True)
    traceback = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_celery_task_dead_letters_created_at", "created_at"),
    )
