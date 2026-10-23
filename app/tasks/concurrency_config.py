"""Per-task-type Celery worker pool configuration (issue #544).

Splits the worker fleet into two dedicated pools so blocking I/O (webhook
HTTP deliveries) never starves CPU-bound calculations (SLA / contract):

  * I/O-bound pool: handles webhook task names. ``eventlet`` green threads
    deliver thousands of HTTP webhooks per process without forking;
    ``concurrency=50`` keeps a high number of concurrent sockets in flight.

  * CPU-bound pool: handles SLA / contract calculation task names.
    ``prefork`` processes isolate each CPU-bound unit of work from greenlet
    I/O so DB round-trips and CPU share are predictable; ``concurrency=4``
    keeps memory and connection-pool pressure low.

Documented worker startup commands
----------------------------------

  # Webhook / I/O-bound worker — one process, 50 greenlet threads:
  celery -A app.tasks.celery_app worker --pool=eventlet --concurrency=50 -Q webhooks

  # SLA / contract calculation worker — 4 forked processes:
  celery -A app.tasks.celery_app worker --pool=prefork --concurrency=4 -Q celery

External adapters (e.g. systemd units, Docker Compose, or K8s) can read
``get_task_pool_config`` to derive the flags per consumed queue.
"""

from typing import Any, Dict, Optional

from app.core.config import settings

# Substrings that classify a fully-qualified task name as I/O-bound.
WEBHOOK_TASK_KEYWORDS = ("webhook",)
# Substrings that classify a fully-qualified task name as CPU-bound.
CALCULATION_TASK_KEYWORDS = (
    "compute",
    "calculate",
    "calculation",
    "contract",
    "sla",
)


def _is_webhook_task(task_name: str) -> bool:
    return any(keyword in task_name for keyword in WEBHOOK_TASK_KEYWORDS)


def _is_calculation_task(task_name: str) -> bool:
    return any(keyword in task_name for keyword in CALCULATION_TASK_KEYWORDS)


def get_task_pool_config(task_name: Optional[str] = None) -> Dict[str, Any]:
    """Return the pool + concurrency dict for a fully-qualified task name.

    I/O-bound (webhook) tasks resolve to the eventlet pool; CPU-bound
    (SLA / contract calculation) tasks resolve to the prefork pool; anything
    else falls back to the CPU-bound pool configuration (Celery's default
    ``prefork`` pool with a conservative concurrency).
    """
    name = (task_name or "").lower()

    if _is_webhook_task(name):
        return {
            "pool": settings.CELERY_WEBHOOK_POOL,
            "concurrency": settings.CELERY_IO_CONCURRENCY,
            "queues": ["webhooks"],
        }

    if _is_calculation_task(name):
        return {
            "pool": settings.CELERY_CALC_POOL,
            "concurrency": settings.CELERY_CPU_CONCURRENCY,
            "queues": ["celery"],
        }

    return {
        "pool": settings.CELERY_CALC_POOL,
        "concurrency": settings.CELERY_CPU_CONCURRENCY,
    }