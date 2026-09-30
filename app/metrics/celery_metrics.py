"""Celery task execution metrics exported to Prometheus (issue #537).

Hooks into Celery task signals so that task execution durations and failure
counts are exported on the existing ``/metrics`` endpoint (the root metrics
router in ``app/metrics/database_metrics.py`` uses ``generate_latest()``,
which includes every collector registered with the default registry).

Exported metrics:

- ``celery_tasks_total``            – counter of tasks that started execution
- ``celery_task_runtime_seconds``   – histogram of task runtimes
- ``celery_tasks_failed``           – counter of tasks that failed

Importing this module registers the signal handlers.  It is imported from
``app.tasks.celery_app`` so that both the API process (eager mode) and the
Celery worker processes export metrics.
"""

import time
from typing import Dict

from prometheus_client import Counter, Histogram
from celery.signals import task_failure, task_postrun, task_prerun

CELERY_TASKS_TOTAL = Counter(
    "celery_tasks_total",
    "Total number of Celery tasks that started execution.",
    labelnames=["task"],
)

CELERY_TASK_RUNTIME_SECONDS = Histogram(
    "celery_task_runtime_seconds",
    "Duration of Celery task execution in seconds.",
    labelnames=["task"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0),
)

CELERY_TASKS_FAILED = Counter(
    "celery_tasks_failed",
    "Total number of Celery tasks that failed.",
    labelnames=["task"],
)

# task_id -> monotonic start timestamp, used to compute runtimes.
_start_times: Dict[str, float] = {}


@task_prerun.connect
def _record_task_start(sender, task_id, task, args, kwargs, **kw) -> None:
    """Increment the tasks-started counter and stamp the run start time."""
    CELERY_TASKS_TOTAL.labels(task=sender.name).inc()
    _start_times[task_id] = time.perf_counter()


@task_postrun.connect
def _record_task_success(sender, task_id, task, args, kwargs, retval, state, **kw) -> None:
    """Record the runtime histogram for tasks that completed successfully."""
    if state == "SUCCESS":
        start = _start_times.pop(task_id, None)
        if start is not None:
            CELERY_TASK_RUNTIME_SECONDS.labels(task=sender.name).observe(
                time.perf_counter() - start
            )


@task_failure.connect
def _record_task_failure(sender, task_id, exception, args, kwargs, traceback, einfo, **kw) -> None:
    """Increment the tasks-failed counter for tasks that raised."""
    CELERY_TASKS_FAILED.labels(task=sender.name).inc()
    _start_times.pop(task_id, None)
