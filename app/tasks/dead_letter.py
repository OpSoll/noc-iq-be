"""Dead-letter queue (DLQ) routing for unhandled Celery task exceptions.

Issue #530: Celery tasks that fail permanently due to unexpected code
exceptions are discarded without dead-letter audit routing. This module:

* publishes the failed task message to the ``celery_dead_letter`` queue
  once its retry budget is exhausted, and
* persists the failed task payload and traceback in the
  ``celery_task_dead_letters`` table for audit and replay.

The module is imported by :mod:`app.tasks.celery_app`, which registers the
``task_failure`` signal handler at import time.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Sequence

from celery import signals
from celery.exceptions import MaxRetriesExceededError

from app.core.config import settings

logger = logging.getLogger(__name__)


def _json_default(value: Any) -> str:
    """Best-effort JSON serialisation (UUIDs, datetimes, objects)."""
    return str(value)


def _serialize_payload(value: Any) -> Optional[str]:
    if value is None:
        return None
    try:
        return json.dumps(value, default=_json_default)
    except (TypeError, ValueError):
        return str(value)


def store_failed_task(
    *,
    task_id: str,
    task_name: str,
    args: Optional[Sequence[Any]] = None,
    kwargs: Optional[Dict[str, Any]] = None,
    exception: Optional[str] = None,
    traceback: Optional[str] = None,
    queue: Optional[str] = None,
) -> None:
    """Persist a permanently failed task's payload and traceback.

    Runs in its own short-lived session so a DB error never breaks the
    worker; failures are logged and swallowed.
    """
    from app.db.session import SessionLocal
    from app.models.orm.celery_task_dead_letter import CeleryTaskDeadLetterORM

    db = SessionLocal()
    try:
        row = CeleryTaskDeadLetterORM(
            task_id=task_id,
            task_name=task_name,
            queue=queue or settings.CELERY_DEAD_LETTER_QUEUE,
            args_json=_serialize_payload(args),
            kwargs_json=_serialize_payload(kwargs),
            exception=exception,
            traceback=traceback,
            created_at=datetime.now(timezone.utc),
        )
        db.add(row)
        db.commit()
        logger.warning(
            "Dead-letter record persisted for task %s (%s): %s",
            task_id,
            task_name,
            exception,
        )
    except Exception:
        logger.exception("Failed to persist dead-letter record for task %s", task_id)
        db.rollback()
    finally:
        db.close()


def publish_to_dead_letter_queue(message: Dict[str, Any]) -> bool:
    """Publish *message* to the ``celery_dead_letter`` queue.

    The queue is declared idempotently before publishing. In eager mode
    (tests / local dev) there is no real broker, so publishing is skipped —
    the in-process audit row is already written by the signal handler.
    """
    if settings.CELERY_TASK_ALWAYS_EAGER:
        logger.debug("Skipping DLQ publish in eager mode")
        return True

    try:
        from kombu import Exchange, Queue

        from app.tasks.celery_app import celery_app

        dlq = Queue(
            settings.CELERY_DEAD_LETTER_QUEUE,
            exchange=Exchange(""),
            routing_key=settings.CELERY_DEAD_LETTER_QUEUE,
        )
        with celery_app.connection_or_acquire() as conn:
            dlq(conn).declare()
            producer = conn.Producer()
            producer.publish(
                message,
                exchange="",
                routing_key=settings.CELERY_DEAD_LETTER_QUEUE,
                serializer="json",
                retry=True,
            )
        logger.warning(
            "Routed failed task %s (%s) to dead-letter queue %s",
            message.get("id"),
            message.get("task"),
            settings.CELERY_DEAD_LETTER_QUEUE,
        )
        return True
    except Exception:
        logger.exception(
            "Failed to publish task %s to dead-letter queue",
            message.get("id"),
        )
        return False


def _is_final_failure(sender: Any, exc: Optional[BaseException] = None) -> bool:
    """True when the failing task will not be retried again.

    The ``task_failure`` signal only fires on genuine failures (retries are
    scheduled via ``Retry``/``autoretry_for`` and do not emit it), so a
    failure is final when:

    * the retry budget is exhausted (``retries >= max_retries``),
    * ``retry`` raised ``MaxRetriesExceededError``, or
    * the task failed on its first attempt without scheduling a retry
      (``retries == 0``) — the unhandled-exception case from issue #530.
    """
    request = getattr(sender, "request", None)
    retries = int(getattr(request, "retries", 0) or 0)
    max_retries = int(getattr(sender, "max_retries", 0) or 0)
    if retries >= max_retries:
        return True
    if retries == 0:
        return True
    return isinstance(exc, MaxRetriesExceededError)


@signals.task_failure.connect
def _route_failed_task_to_dead_letter(
    sender: Any = None,
    task_id: Optional[str] = None,
    args: Optional[Sequence[Any]] = None,
    kwargs: Optional[Dict[str, Any]] = None,
    exc: Optional[BaseException] = None,
    einfo: Any = None,
    **_: Any,
) -> None:
    """Route permanently failed tasks to the DLQ and persist for audit."""
    if not settings.CELERY_DEAD_LETTER_ENABLED:
        return

    if not _is_final_failure(sender, exc):
        # Retry budget not exhausted yet — the task may still succeed.
        return

    task_name = getattr(sender, "name", None) or "unknown"
    exception_text = f"{type(exc).__name__}: {exc}" if exc is not None else None
    tb = getattr(einfo, "traceback", None) or ""

    # Persist payload + traceback for audit before publishing.
    store_failed_task(
        task_id=task_id or "unknown",
        task_name=task_name,
        args=args,
        kwargs=kwargs,
        exception=exception_text,
        traceback=tb,
    )

    publish_to_dead_letter_queue(
        {
            "task": task_name,
            "id": task_id,
            "args": args,
            "kwargs": kwargs,
            "exception": exception_text,
            "traceback": tb,
            "dead_lettered_at": datetime.now(timezone.utc).isoformat(),
        }
    )
