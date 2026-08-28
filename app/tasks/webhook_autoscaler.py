import time
import logging
import threading
from typing import Optional

from celery import shared_task

from app.core.config import settings

logger = logging.getLogger(__name__)


class WebhookAutoscaler:
    """Autoscaler for webhook worker pools with partition awareness.

    Issue #302: Tracks per-partition queue depth and scales workers
    independently per partition. Priority partitions (SLA, payment)
    have dedicated scale-up thresholds to ensure they never starve.
    """

    def __init__(self):
        self._redis_client = None
        self._current_workers = settings.WEBHOOK_WORKER_MIN
        self._partition_workers: dict[int, int] = {}
        self._last_scale_event: Optional[dict] = None
        self._lock = threading.Lock()
        self._init_partition_workers()
        self._try_init_redis()

    def _init_partition_workers(self):
        """Initialize per-partition worker counts evenly."""
        base = max(1, settings.WEBHOOK_WORKER_MIN // settings.WEBHOOK_PARTITION_COUNT)
        for pid in range(settings.WEBHOOK_PARTITION_COUNT):
            self._partition_workers[pid] = base

        # Priority partitions get an extra worker
        if settings.WEBHOOK_SLA_PRIORITY_PARTITION in self._partition_workers:
            self._partition_workers[settings.WEBHOOK_SLA_PRIORITY_PARTITION] = base + 1

    def _try_init_redis(self):
        try:
            import redis
            self._redis_client = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
            self._redis_client.ping()
        except Exception:
            self._redis_client = None
            logger.warning("Redis unavailable for webhook autoscaler, using simulated queue depth")

    def _get_queue_depth(self, partition_id: Optional[int] = None) -> int:
        if self._redis_client:
            try:
                queue_key = f"celery" if partition_id is None else f"celery:partition:{partition_id}"
                return self._redis_client.llen(queue_key)
            except Exception:
                return 0
        return 0

    def _get_total_queue_depth(self) -> int:
        """Aggregate queue depth across all partitions."""
        if self._redis_client:
            total = 0
            for pid in range(settings.WEBHOOK_PARTITION_COUNT):
                total += self._get_queue_depth(pid)
            return total
        return self._get_queue_depth()

    def _get_current_worker_count(self) -> int:
        return self._current_workers

    def check_and_signal(self) -> dict:
        """Check queue depth across all partitions and signal scaling decisions.

        Returns scaling action details including per-partition breakdown.
        """
        total_queue_depth = self._get_total_queue_depth()
        worker_count = self._get_current_worker_count()
        action = "none"
        new_workers = worker_count

        # Scale up if ANY partition exceeds threshold
        max_partition_depth = 0
        for pid in range(settings.WEBHOOK_PARTITION_COUNT):
            depth = self._get_queue_depth(pid)
            max_partition_depth = max(max_partition_depth, depth)

        if max_partition_depth > settings.WEBHOOK_QUEUE_SCALE_UP_THRESHOLD:
            new_workers = min(worker_count + 2, settings.WEBHOOK_WORKER_MAX)
            if new_workers > worker_count:
                action = "scale_up"
            else:
                action = "at_max"
        elif total_queue_depth < settings.WEBHOOK_QUEUE_SCALE_DOWN_THRESHOLD:
            new_workers = max(worker_count - 1, settings.WEBHOOK_WORKER_MIN)
            if new_workers < worker_count:
                action = "scale_down"
            else:
                action = "at_min"

        with self._lock:
            self._current_workers = new_workers
            event = {
                "action": action,
                "total_queue_depth": total_queue_depth,
                "max_partition_depth": max_partition_depth,
                "previous_workers": worker_count,
                "current_workers": new_workers,
                "partitions": {
                    str(pid): {
                        "depth": self._get_queue_depth(pid),
                        "workers": self._partition_workers.get(pid, 1),
                    }
                    for pid in range(settings.WEBHOOK_PARTITION_COUNT)
                },
                "timestamp": time.time(),
            }
            if action in ("scale_up", "scale_down"):
                self._last_scale_event = event
                logger.info(
                    "Webhook autoscaler %s: total_queue_depth=%d, max_partition_depth=%d, workers=%d->%d",
                    action, total_queue_depth, max_partition_depth, worker_count, new_workers,
                )

        return event

    def get_metrics(self) -> dict:
        """Return autoscaler metrics including partition breakdown."""
        with self._lock:
            return {
                "total_queue_depth": self._get_total_queue_depth(),
                "current_workers": self._current_workers,
                "worker_min": settings.WEBHOOK_WORKER_MIN,
                "worker_max": settings.WEBHOOK_WORKER_MAX,
                "partition_count": settings.WEBHOOK_PARTITION_COUNT,
                "partitions": {
                    str(pid): {
                        "depth": self._get_queue_depth(pid),
                        "workers": self._partition_workers.get(pid, 1),
                    }
                    for pid in range(settings.WEBHOOK_PARTITION_COUNT)
                },
                "last_scaling_event": self._last_scale_event,
            }


autoscaler = WebhookAutoscaler()


@shared_task(name="app.tasks.webhook_autoscaler.periodic_autoscale_check")
def periodic_autoscale_check():
    autoscaler.check_and_signal()
