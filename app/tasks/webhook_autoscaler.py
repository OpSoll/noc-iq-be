import time
import logging
import threading
from typing import Optional

from celery import shared_task

from app.core.config import settings

logger = logging.getLogger(__name__)


class WebhookAutoscaler:
    def __init__(self):
        self._redis_client = None
        self._current_workers = settings.WEBHOOK_WORKER_MIN
        self._last_scale_event: Optional[dict] = None
        self._lock = threading.Lock()
        self._try_init_redis()

    def _try_init_redis(self):
        try:
            import redis
            self._redis_client = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
            self._redis_client.ping()
        except Exception:
            self._redis_client = None
            logger.warning("Redis unavailable for webhook autoscaler, using simulated queue depth")

    def _get_queue_depth(self) -> int:
        if self._redis_client:
            try:
                return self._redis_client.llen("celery")
            except Exception:
                return 0
        return 0

    def _get_current_worker_count(self) -> int:
        return self._current_workers

    def check_and_signal(self) -> dict:
        queue_depth = self._get_queue_depth()
        worker_count = self._get_current_worker_count()
        action = "none"
        new_workers = worker_count

        if queue_depth > settings.WEBHOOK_QUEUE_SCALE_UP_THRESHOLD:
            new_workers = min(worker_count + 1, settings.WEBHOOK_WORKER_MAX)
            if new_workers > worker_count:
                action = "scale_up"
            else:
                action = "at_max"
        elif queue_depth < settings.WEBHOOK_QUEUE_SCALE_DOWN_THRESHOLD:
            new_workers = max(worker_count - 1, settings.WEBHOOK_WORKER_MIN)
            if new_workers < worker_count:
                action = "scale_down"
            else:
                action = "at_min"

        with self._lock:
            self._current_workers = new_workers
            event = {
                "action": action,
                "queue_depth": queue_depth,
                "previous_workers": worker_count,
                "current_workers": new_workers,
                "timestamp": time.time(),
            }
            if action in ("scale_up", "scale_down"):
                self._last_scale_event = event
                logger.info(
                    "Webhook autoscaler %s: queue_depth=%d, workers=%d->%d",
                    action, queue_depth, worker_count, new_workers,
                )

        return event

    def get_metrics(self) -> dict:
        with self._lock:
            return {
                "queue_depth": self._get_queue_depth(),
                "current_workers": self._current_workers,
                "worker_min": settings.WEBHOOK_WORKER_MIN,
                "worker_max": settings.WEBHOOK_WORKER_MAX,
                "last_scaling_event": self._last_scale_event,
            }


autoscaler = WebhookAutoscaler()


@shared_task(name="app.tasks.webhook_autoscaler.periodic_autoscale_check")
def periodic_autoscale_check():
    autoscaler.check_and_signal()
