from __future__ import annotations

import logging
import sys
from datetime import datetime
from typing import Dict, List

from celery import Celery, Task
from celery.signals import worker_ready

from app.core.config import settings

logger = logging.getLogger(__name__)

celery_app = Celery(
    "nociq",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=[
        "app.tasks.sla_tasks",
        "app.tasks.webhook_tasks",
        "app.tasks.idempotency_tasks",
        "app.tasks.timeout_guard",
        "app.tasks.dead_letter",
        "app.tasks.worker_health",
        "app.tasks.payment_tasks",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_trace_propagators=[
        "opentelemetry.instrumentation.celery.propagator",
    ],
    task_track_started=True,
    task_acks_late=True,
    # Issue #530: reject (and redeliver) messages whose worker process died
    # mid-execution so they can be retried elsewhere instead of silently
    # vanishing. Requires acks_late (set above).
    task_reject_on_worker_lost=True,
    # Issue #531: execution time limits. Soft limit lets tasks clean up
    # gracefully (SoftTimeLimitExceeded); hard limit SIGKILLs the worker.
    task_soft_time_limit=settings.CELERY_TASK_SOFT_TIME_LIMIT,
    task_time_limit=settings.CELERY_TASK_TIME_LIMIT,
    task_always_eager=settings.CELERY_TASK_ALWAYS_EAGER,
    task_store_eager_result=True,
    worker_prefetch_multiplier=1,
    result_expires=86400,  # 24 hours

    beat_schedule={
        "verify-payment-transactions": {
            "task": "app.tasks.payment_tasks.verify_payment_transactions",
            "schedule": 300.0,  # every 5 minutes
        },
        # Operator wallet balance threshold monitor: reads XLM/USDC balances
        # every 15 minutes and alerts before a drained settlement wallet
        # starts failing SLA payouts.
        "monitor-wallet-balances": {
            "task": "app.tasks.payment_tasks.monitor_wallet_balances",
            "schedule": float(settings.WALLET_BALANCE_CHECK_INTERVAL_SECONDS),
        },
        "webhook-autoscale-check": {
            "task": "app.tasks.webhook_autoscaler.periodic_autoscale_check",
            "schedule": 30.0,
        },
        "retry-pending-webhook-deliveries": {
            "task": "app.tasks.webhook_tasks.retry_pending_webhook_deliveries",
            "schedule": 60.0,
        },
        "cleanup-expired-idempotency-keys": {
            "task": "app.tasks.idempotency_tasks.cleanup_expired_idempotency_keys",
            "schedule": 3600.0,  # every hour
        },
        # BE-W5-055: periodic DB pool + broker connection guardrail check.
        # Runs every 60s alongside the existing beats; emits a WARNING log
        # line and flips ``guardrail.alert.*`` gauges when saturation
        # thresholds are crossed.
        "concurrency-guardrail-check": {
            "task": "app.tasks.celery_app.guardrail_check_task",
            "schedule": 60.0,
        },
        # Issue #531: periodically revoke tasks that are still marked STARTED
        # after their lease (time limit) expired so hung executions cannot
        # pin workers indefinitely.
        "revoke-hung-tasks": {
            "task": "app.tasks.timeout_guard.revoke_hung_tasks",
            "schedule": 60.0,
        },
        # Delivery log retention purging: delete webhook delivery logs
        # older than WEBHOOK_DELIVERY_LOG_RETENTION_DAYS (default 30 days).
        "purge-old-webhook-logs": {
            "task": "app.tasks.webhook_tasks.purge_old_webhook_logs",
            "schedule": 86400.0,  # every 24 hours
        },
        # Issue #536: worker heartbeat monitor — pings the worker fleet every
        # 60s, persists status to Redis, alerts when no worker responds.
        "ping-celery-workers": {
            "task": "app.tasks.worker_health.ping_celery_workers",
            "schedule": 60.0,
        },
    },
)

# Issue #530: register the task_failure signal handler that routes
# permanently failed tasks to the dead-letter queue and persists their
# payload + traceback for audit. Importing the module wires the signal.
from app.tasks import dead_letter as _dead_letter  # noqa: E402,F401

# Issue #537: register Celery task Prometheus metrics signal handlers so
# both the API process (eager mode) and worker processes export metrics on
# the existing ``/metrics`` endpoint. Importing the module wires the signals.
import app.metrics.celery_metrics  # noqa: E402,F401  (side-effect import)


celery_app.autodiscover_tasks(["app.tasks"])


# Issue #544: dedicated worker pools per task type. I/O-bound webhook tasks
# run on an eventlet worker (high concurrency); CPU-bound SLA/contract
# calculations run on a small prefork worker. Startup commands:
#   celery -A app.tasks.celery_app worker --pool=eventlet --concurrency=50 -Q webhooks
#   celery -A app.tasks.celery_app worker --pool=prefork --concurrency=4 -Q celery
# See ``app.tasks.concurrency_config.get_task_pool_config`` for the resolver.


def _mark_job_timed_out(task_id: str, code: str) -> None:
    """Mark the tracked ``Job`` row for ``task_id`` as failed on time limit.

    Issue #531: used by the soft/hard time-limit hooks so a timed-out task is
    surfaced in the jobs API instead of hanging as STARTED forever.
    """
    try:
        from app.db.session import SessionLocal
        from app.models.job import Job, JobStatus

        db = SessionLocal()
        try:
            job = db.query(Job).filter(Job.celery_task_id == task_id).first()
            if job and job.status == JobStatus.STARTED:
                job.status = JobStatus.FAILURE
                job.error = f"{code}: task exceeded its execution time limit"
                job.error_code = code
                job.error_retryable = False
                job.finished_at = datetime.utcnow()
                job.heartbeat_at = None
                job.lease_expires_at = None
                db.commit()
        finally:
            db.close()
    except Exception:
        logger.exception("Failed to mark task %s as timed out (%s)", task_id, code)


class GuardedTask(Task):
    """Task base with execution time-limit guards.

    Issue #531: long-running tasks (e.g. stalled Stellar RPC network
    fetches) can hang Celery workers indefinitely. Tasks inheriting this
    base get graceful cleanup hooks for the soft and hard time limits.
    """

    abstract = True

    def on_soft_time_limit(self, exc, task_id, args, kwargs):  # type: ignore[override]
        logger.warning(
            "Soft time limit exceeded for task %s (%ds) — cleaning up gracefully",
            task_id,
            settings.CELERY_TASK_SOFT_TIME_LIMIT,
        )
        _mark_job_timed_out(task_id, "SOFT_TIME_LIMIT")

    def on_time_limit(self, exc, task_id, args, kwargs):  # type: ignore[override]
        logger.error(
            "Hard time limit exceeded for task %s (%ds) — worker process killed",
            task_id,
            settings.CELERY_TASK_TIME_LIMIT,
        )
        _mark_job_timed_out(task_id, "HARD_TIME_LIMIT")


def _required_queue_names() -> List[str]:
    """Parse CELERY_REQUIRED_QUEUES (comma-separated) into a list of names."""
    return [
        name.strip()
        for name in settings.CELERY_REQUIRED_QUEUES.split(",")
        if name.strip()
    ]


def verify_queue_bindings(
    timeout: float | None = None,
    strict: bool | None = None,
) -> Dict[str, object]:
    """Verify that all required queues are bound to active workers.

    BE-W5-051: Worker bootstrap fails fast on missing queue/exchange dependencies.
    Operational logs clearly identify failed prerequisite checks.

    Returns a dict with keys:
        - ok (bool): True if all required queues are present.
        - required (List[str]): Required queue names that were checked.
        - observed (List[str]): Names of queues reported by active workers.
        - missing (List[str]): Required queues that were not observed.
        - workers_seen (int): Number of active workers that responded.
        - timeout_seconds (float): Probe timeout used.

    When ``strict`` is True (the default in production), a missing required
    queue causes a ``RuntimeError`` to be raised so callers can decide how
    to fail fast (e.g. ``sys.exit(1)`` from the worker-ready signal).
    """
    probe_timeout = (
        timeout if timeout is not None else settings.CELERY_QUEUE_PROBE_TIMEOUT_SECONDS
    )
    strict_flag = strict if strict is not None else settings.CELERY_STRICT_QUEUE_BINDINGS

    required = _required_queue_names()
    result: Dict[str, object] = {
        "ok": True,
        "required": required,
        "observed": [],
        "missing": [],
        "workers_seen": 0,
        "timeout_seconds": probe_timeout,
    }

    if not required:
        # Nothing configured — treat as a no-op success.
        return result

    try:
        inspect = celery_app.control.inspect(timeout=probe_timeout)
        active_queues = inspect.active_queues() or {}
    except Exception as exc:  # broker unreachable, connection refused, etc.
        logger.error(
            "BE-W5-051: queue binding probe failed to reach broker: %s",
            exc,
        )
        result["ok"] = False
        result["missing"] = list(required)
        if strict_flag:
            raise RuntimeError(
                f"BE-W5-051: celery broker unreachable while verifying "
                f"queues {required}: {exc}"
            ) from exc
        return result

    observed: List[str] = []
    for worker_name, queues in active_queues.items():
        result["workers_seen"] = int(result["workers_seen"]) + 1  # type: ignore[arg-type]
        if not isinstance(queues, list):
            continue
        for q in queues:
            name = (q or {}).get("name") if isinstance(q, dict) else None
            if isinstance(name, str) and name:
                observed.append(name)

    missing = [name for name in required if name not in observed]
    result["observed"] = sorted(set(observed))
    result["missing"] = missing

    if missing:
        logger.error(
            "BE-W5-051: missing required queue bindings: required=%s observed=%s "
            "workers_seen=%d timeout=%.1fs",
            required,
            result["observed"],
            result["workers_seen"],
            probe_timeout,
        )
        result["ok"] = False
        if strict_flag:
            raise RuntimeError(
                f"BE-W5-051: required queues not bound to any worker: {missing}"
            )
    else:
        logger.info(
            "BE-W5-051: queue binding probe OK — required=%s observed=%s "
            "workers_seen=%d",
            required,
            result["observed"],
            result["workers_seen"],
        )

    return result


@worker_ready.connect
def _on_worker_ready(sender=None, **kwargs) -> None:  # noqa: ANN001
    """Celery ``worker_ready`` hook — fail fast if required queues are missing.

    BE-W5-051: Worker bootstrap fails fast on missing queue/exchange dependencies.
    Only enforces strict mode in real workers; in eager mode this is a no-op.
    """
    if settings.CELERY_TASK_ALWAYS_EAGER:
        logger.debug("BE-W5-051: skipping queue probe in eager mode")
        return

    try:
        verify_queue_bindings()
    except RuntimeError as exc:
        logger.critical("BE-W5-051: worker bootstrap aborted — %s", exc)
        # Fail fast so the orchestrator sees the worker crash and intervenes.
        sys.exit(1)


@celery_app.task(name="app.tasks.celery_app.guardrail_check_task")
def guardrail_check_task() -> Dict[str, object]:
    """Periodic DB + broker saturation guardrail evaluation.

    BE-W5-055: returns the live readings; emits WARNING log lines and sets
    ``guardrail.alert.*`` gauges via ``evaluate_guardrails``. Runs from a
    beat schedule every 60 s.
    """
    # Local import avoids a circular dep at module-import time
    # (concurrency_guardrails imports ... → celery_app imports).
    from app.services.concurrency_guardrails import evaluate_guardrails
    return evaluate_guardrails(celery_app)