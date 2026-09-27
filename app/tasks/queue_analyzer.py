"""#356 – Async queue partition hot-spot analysis and rebalance plan.

Provides:
* ``QueueAnalyzer`` – inspects Celery queue depths and identifies hot-spot
  partitions.
* A ``GET /metrics/queue-analysis`` FastAPI router.
* A periodic Celery beat task (every 5 min) that logs findings.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter

from app.core.config import settings
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default partition list – mirrors the queue names used by workers.
# Override via a ``CELERY_PARTITIONS`` setting if your deployment differs.
# ---------------------------------------------------------------------------

_DEFAULT_PARTITIONS: list[str] = [
    "default",
    "sla",
    "webhooks",
    "analytics",
]

PARTITIONS: list[str] = getattr(settings, "CELERY_PARTITIONS", _DEFAULT_PARTITIONS)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class PartitionDepth:
    partition: str
    depth: int


@dataclass
class Hotspot:
    partition: str
    depth: int
    avg_depth: float
    ratio: float


@dataclass
class RebalanceSuggestion:
    source: str
    target: str
    move_count: int
    reason: str


@dataclass
class AnalysisResult:
    timestamp: str
    partitions: list[PartitionDepth]
    total_depth: int
    avg_depth: float
    hotspots: list[Hotspot]
    suggestions: list[RebalanceSuggestion]


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------

class QueueAnalyzer:
    """Reads queue depths from the Celery broker and reports hot-spots."""

    def __init__(
        self,
        partitions: list[str] | None = None,
        hotspot_threshold: float = 2.0,
    ) -> None:
        self._partitions = partitions or PARTITIONS
        self._threshold = hotspot_threshold

    # -- data collection -----------------------------------------------------

    def _get_queue_depth(self, queue_name: str) -> int:
        """Return the number of pending messages in *queue_name*.

        Uses the ``length()`` method of the Celery broker transport, which
        falls back to a ``LLEN`` call on the underlying Redis list.
        """
        try:
            conn = celery_app.connection_or_acquire()
            queue = conn.default_channel.queues(queue_name)
            return queue.length()
        except Exception:
            # If the broker is unreachable we return 0 so the rest of the
            # pipeline can still function.
            logger.warning("Could not read depth for queue '%s'", queue_name)
            return 0

    def analyze(self) -> AnalysisResult:
        """Build a full analysis snapshot."""
        depths = [PartitionDepth(partition=p, depth=self._get_queue_depth(p)) for p in self._partitions]
        total = sum(d.depth for d in depths)
        avg = total / len(depths) if depths else 0.0

        hotspots = self.get_hotspots(depths, avg)
        suggestions = self.suggest_rebalance(depths, hotspots)

        return AnalysisResult(
            timestamp=datetime.now(timezone.utc).isoformat(),
            partitions=depths,
            total_depth=total,
            avg_depth=avg,
            hotspots=hotspots,
            suggestions=suggestions,
        )

    # -- hot-spot detection --------------------------------------------------

    def get_hotspots(
        self,
        depths: list[PartitionDepth] | None = None,
        avg: float | None = None,
    ) -> list[Hotspot]:
        """Return partitions whose depth exceeds ``threshold × average``."""
        if depths is None:
            analysis = self.analyze()
            return analysis.hotspots

        if avg is None:
            avg = sum(d.depth for d in depths) / len(depths) if depths else 0.0

        hotspots: list[Hotspot] = []
        for d in depths:
            ratio = d.depth / avg if avg > 0 else 0.0
            if ratio > self._threshold:
                hotspots.append(
                    Hotspot(
                        partition=d.partition,
                        depth=d.depth,
                        avg_depth=avg,
                        ratio=round(ratio, 2),
                    )
                )
        return hotspots

    # -- rebalance suggestions -----------------------------------------------

    def suggest_rebalance(
        self,
        depths: list[PartitionDepth] | None = None,
        hotspots: list[Hotspot] | None = None,
    ) -> list[RebalanceSuggestion]:
        """Produce actionable rebalance recommendations."""
        if depths is None:
            analysis = self.analyze()
            return analysis.suggestions

        if hotspots is None:
            hotspots = self.get_hotspots(depths)

        avg = sum(d.depth for d in depths) / len(depths) if depths else 0.0
        suggestions: list[RebalanceSuggestion] = []
        depth_map = {d.partition: d.depth for d in depths}

        for hs in hotspots:
            target = min(depth_map, key=depth_map.get)  # type: ignore[arg-type]
            move_count = max(0, (hs.depth - int(avg)) // 2)
            suggestions.append(
                RebalanceSuggestion(
                    source=hs.partition,
                    target=target,
                    move_count=move_count,
                    reason=(
                        f"Partition '{hs.partition}' depth ({hs.depth}) is "
                        f"{hs.ratio}× the average ({avg:.0f}). "
                        f"Consider moving ~{move_count} tasks to '{target}'."
                    ),
                )
            )

        return suggestions

    # -- to-dict helpers for API serialisation -------------------------------

    @staticmethod
    def result_to_dict(result: AnalysisResult) -> dict[str, Any]:
        return {
            "timestamp": result.timestamp,
            "total_depth": result.total_depth,
            "avg_depth": round(result.avg_depth, 2),
            "partitions": [
                {"partition": d.partition, "depth": d.depth}
                for d in result.partitions
            ],
            "hotspots": [
                {
                    "partition": h.partition,
                    "depth": h.depth,
                    "avg_depth": round(h.avg_depth, 2),
                    "ratio": h.ratio,
                }
                for h in result.hotspots
            ],
            "suggestions": [
                {
                    "source": s.source,
                    "target": s.target,
                    "move_count": s.move_count,
                    "reason": s.reason,
                }
                for s in result.suggestions
            ],
        }


# ---------------------------------------------------------------------------
# /metrics/queue-analysis endpoint
# ---------------------------------------------------------------------------

analyzer = QueueAnalyzer()

queue_analysis_router = APIRouter(tags=["metrics"])


@queue_analysis_router.get("/metrics/queue-analysis")
def queue_analysis_endpoint() -> dict[str, Any]:
    """Return live queue partition depths, hot-spots, and rebalance suggestions."""
    result = analyzer.analyze()
    return QueueAnalyzer.result_to_dict(result)


# ---------------------------------------------------------------------------
# Periodic Celery beat task
# ---------------------------------------------------------------------------

@celery_app.task(name="app.tasks.queue_analyzer.analyze_queue_hotspots")
def analyze_queue_hotspots() -> None:
    """Beat task: run queue analysis every 5 minutes and log findings."""
    result = analyzer.analyze()
    data = QueueAnalyzer.result_to_dict(result)

    if result.hotspots:
        logger.warning(
            "Queue hot-spots detected: %s",
            [(h.partition, h.ratio) for h in result.hotspots],
        )
    for s in result.suggestions:
        logger.info("Rebalance suggestion: %s", s.reason)

    logger.info("Queue analysis snapshot: total_depth=%d, avg=%.1f", data["total_depth"], data["avg_depth"])


# Register the beat schedule (augments the existing schedule in celery_app.py)
celery_app.conf.beat_schedule["analyze-queue-hotspots"] = {
    "task": "app.tasks.queue_analyzer.analyze_queue_hotspots",
    "schedule": 300.0,  # every 5 minutes
}
