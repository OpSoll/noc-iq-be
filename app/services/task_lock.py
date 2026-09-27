"""Redis-backed task lock for Celery task idempotency deduplication.

Issue #533: duplicate Celery task triggers (e.g. an SLA computation job
submitted twice) can cause the same computation to run simultaneously on
different workers. :func:`RedisTaskLock` acquires a Redis key
``lock:task:<job_id>`` with ``SET NX EX`` for the duration of the task and
skips duplicate executions while the lock is held.

The lock TTL acts as a safety net: if a worker crashes mid-task, the key
expires on its own instead of pinning the job forever.
"""
from __future__ import annotations

import inspect
import logging
import uuid
from functools import wraps
from typing import Any, Callable, Dict, Optional, Tuple, Union

from app.core.config import settings

logger = logging.getLogger(__name__)

TASK_LOCK_PREFIX = "lock:task:"
DEFAULT_TASK_LOCK_TTL_SECONDS = 3600


def _build_call_arguments(
    fn: Callable[..., Any],
    args: Tuple[Any, ...],
    kwargs: Dict[str, Any],
) -> Dict[str, Any]:
    """Bind ``args``/``kwargs`` to ``fn``'s signature.

    Returns a dict of parameter name → value (including defaults) so
    format-string lock keys like ``"sla:{device_id}:{period}"`` can resolve
    against the wrapped task's actual arguments (including the injected
    ``self`` for ``bind=True`` tasks). Falls back to raw kwargs when the
    signature cannot be bound.
    """
    try:
        return inspect.signature(fn).bind(*args, **kwargs).arguments
    except (TypeError, ValueError):
        return dict(kwargs)


def _redis_client(redis_url: Optional[str] = None) -> Any:
    """Build a decode-responses Redis client (defaults to ``settings.REDIS_URL``)."""
    import redis

    return redis.Redis.from_url(redis_url or settings.REDIS_URL, decode_responses=True)


def RedisTaskLock(
    lock_key: Union[str, Callable[[Dict[str, Any]], str]],
    ttl_seconds: int = DEFAULT_TASK_LOCK_TTL_SECONDS,
    redis_url: Optional[str] = None,
    fail_open: bool = True,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Deduplicate Celery task executions with a Redis lock.

    Args:
        lock_key: Either a static job-id string, a ``str.format`` template
            resolved against the wrapped function's bound arguments (e.g.
            ``"sla:{device_id}:{period}"``), or a callable receiving the
            bound-argument dict and returning the job id. The full Redis key
            becomes ``lock:task:<job_id>``.
        ttl_seconds: Lock TTL in seconds. Acts as the safety-net expiry so a
            crashed worker never pins the key forever.
        redis_url: Optional Redis URL override (defaults to
            ``settings.REDIS_URL``).
        fail_open: When True (default), a Redis outage logs a warning and the
            task still runs — deduplication is best-effort and must never
            block SLA processing.

    Returns:
        The wrapped function's result, or — when the lock is already held —
        a dict marker ``{"skipped": True, "reason": "duplicate_task_lock_held",
        "lock_key": <key>}`` so duplicate executions are visibly skipped.
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            call_args = _build_call_arguments(fn, args, kwargs)

            if callable(lock_key):
                job_id = lock_key(call_args)
            elif isinstance(lock_key, str) and "{" in lock_key:
                job_id = lock_key.format(**call_args)
            else:
                job_id = lock_key

            full_key = f"{TASK_LOCK_PREFIX}{job_id}"

            try:
                client = _redis_client(redis_url)
                token = uuid.uuid4().hex
                acquired = client.set(full_key, token, nx=True, ex=ttl_seconds)
            except Exception as exc:  # Redis unreachable — do not block the task
                logger.warning(
                    "RedisTaskLock: Redis unavailable for %s (%s); running %s "
                    "without a lock",
                    full_key,
                    exc,
                    fn.__name__,
                )
                if not fail_open:
                    raise
                return fn(*args, **kwargs)

            if not acquired:
                logger.warning(
                    "RedisTaskLock: %s already held — skipping duplicate "
                    "execution of %s",
                    full_key,
                    fn.__name__,
                )
                return {
                    "skipped": True,
                    "reason": "duplicate_task_lock_held",
                    "lock_key": full_key,
                }

            try:
                return fn(*args, **kwargs)
            finally:
                # Release only if we still own the lock (token match) so a
                # TTL-expired-and-reacquired lock is not deleted out from
                # under its new holder.
                try:
                    if client.get(full_key) == token:
                        client.delete(full_key)
                except Exception as exc:  # pragma: no cover - defensive
                    logger.warning(
                        "RedisTaskLock: failed to release %s (%s); TTL will "
                        "expire it",
                        full_key,
                        exc,
                    )

        return wrapper

    return decorator
