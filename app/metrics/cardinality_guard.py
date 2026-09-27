import time
import threading
import logging
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)


class CardinalityGuard:
    def __init__(self):
        self._budget = settings.METRICS_CARDINALITY_BUDGET
        self._series: dict[str, dict[str, int]] = {}
        self._lock = threading.Lock()

    def _evict_least_used(self, metric_name: str):
        if metric_name not in self._series:
            return
        series = self._series[metric_name]
        if len(series) <= self._budget:
            return
        sorted_keys = sorted(series.keys(), key=lambda k: series[k])
        evict_count = max(len(series) - self._budget, settings.RATE_LIMIT_EVICT_BATCH_SIZE)
        for k in sorted_keys[:evict_count]:
            del series[k]
            logger.warning("Cardinality budget exceeded for metric '%s', evicted series: %s", metric_name, k)

    def record(self, metric_name: str, labels_key: str):
        with self._lock:
            if metric_name not in self._series:
                self._series[metric_name] = {}
            if labels_key not in self._series[metric_name]:
                if len(self._series[metric_name]) >= self._budget:
                    self._evict_least_used(metric_name)
                if len(self._series[metric_name]) >= self._budget:
                    logger.warning("Cardinality budget (%d) exceeded for metric '%s', dropping series: %s", self._budget, metric_name, labels_key)
                    return False
            self._series[metric_name][labels_key] = self._series[metric_name].get(labels_key, 0) + 1
        return True

    def get_cardinality(self) -> dict:
        with self._lock:
            return {
                metric: {"unique_series": len(series), "budget": self._budget}
                for metric, series in self._series.items()
            }

    def wrap_counter(self, name: str, labels_key: str, value: int = 1) -> bool:
        return self.record(name, labels_key)

    def wrap_histogram(self, name: str, labels_key: str, value: float = 0) -> bool:
        return self.record(name, labels_key)

    def wrap_gauge(self, name: str, labels_key: str, value: float = 0) -> bool:
        return self.record(name, labels_key)


cardinality_guard = CardinalityGuard()
