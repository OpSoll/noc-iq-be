import logging
import sys
from typing import Dict, List

from celery import Celery
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
    task_always_eager=settings.CELERY_TASK_ALWAYS_EAGER,
    task_store_eager_result=True,
    worker_prefetch_multiplier=1,
    result_expires=86400,  # 24 hours

    beat_schedule={
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
    },
)

celery_app.autodiscover_tasks(["app.tasks"])


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
