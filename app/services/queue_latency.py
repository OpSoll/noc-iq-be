"""Queue latency histogram instrumentation for SLA and webhook pipelines (#314)."""

import time
from typing import Dict, Optional
from app.services.metrics import metrics


class QueueLatencyTracker:
    """Track enqueue-to-start and start-to-finish latency for async jobs."""

    def __init__(self, job_type: str):
        self.job_type = job_type
        self.enqueued_at: Optional[float] = None
        self.started_at: Optional[float] = None

    def mark_enqueued(self):
        self.enqueued_at = time.time()

    def mark_started(self):
        self.started_at = time.time()
        if self.enqueued_at:
            wait_ms = (self.started_at - self.enqueued_at) * 1000
            metrics.record_histogram(
                f"{self.job_type}_queue_wait_ms", wait_ms,
                {"job_type": self.job_type}
            )

    def mark_finished(self):
        if self.started_at:
            duration_ms = (time.time() - self.started_at) * 1000
            metrics.record_histogram(
                f"{self.job_type}_execution_ms", duration_ms,
                {"job_type": self.job_type}
            )
