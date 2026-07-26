from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class WindowSize(str, Enum):
    HOURLY = "hourly"
    DAILY = "daily"
    WEEKLY = "weekly"


_WINDOW_DELTAS: Dict[WindowSize, timedelta] = {
    WindowSize.HOURLY: timedelta(hours=1),
    WindowSize.DAILY: timedelta(days=1),
    WindowSize.WEEKLY: timedelta=timedelta(weeks=1),
}


def _align_to_window(dt: datetime, window: WindowSize) -> datetime:
    """Align a UTC datetime to the start of its enclosing window boundary."""
    if window == WindowSize.HOURLY:
        return dt.replace(minute=0, second=0, microsecond=0)
    if window == WindowSize.DAILY:
        return dt.replace(hour=0, minute=0, second=0, microsecond=0)
    if window == WindowSize.WEEKLY:
        aligned = dt.replace(hour=0, minute=0, second=0, microsecond=0)
        days_since_monday = aligned.weekday()
        return aligned - timedelta(days=days_since_monday)
    return dt


class TrendAggregator:
    """Aggregate time-series metrics into aligned windows.

    All timestamps are normalised to UTC before bucketing so DST transitions
    do not affect window alignment.
    """

    @staticmethod
    def aggregate(
        metrics: List[Dict[str, Any]],
        window_size: str,
        from_dt: Optional[datetime] = None,
        to_dt: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        window = WindowSize(window_size)
        delta = _WINDOW_DELTAS[window]

        # Normalise all timestamps to UTC
        normalised: List[Dict[str, Any]] = []
        for m in metrics:
            ts = m.get("timestamp")
            if isinstance(ts, datetime):
                if ts.tzinfo is not None:
                    ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
            else:
                continue
            normalised.append({**m, "timestamp": ts})

        if from_dt and from_dt.tzinfo is not None:
            from_dt = from_dt.astimezone(timezone.utc).replace(tzinfo=None)
        if to_dt and to_dt.tzinfo is not None:
            to_dt = to_dt.astimezone(timezone.utc).replace(tzinfo=None)

        buckets: Dict[datetime, List[float]] = defaultdict(list)

        for m in normalised:
            ts: datetime = m["timestamp"]
            if from_dt and ts < from_dt:
                continue
            if to_dt and ts >= to_dt:
                continue
            bucket_start = _align_to_window(ts, window)
            value = m.get("value", 0.0)
            try:
                buckets[bucket_start].append(float(value))
            except (TypeError, ValueError):
                continue

        result: List[Dict[str, Any]] = []
        for bucket_start in sorted(buckets):
            values = buckets[bucket_start]
            result.append(
                {
                    "window": window.value,
                    "bucket_start": bucket_start.isoformat() + "Z",
                    "bucket_end": (bucket_start + delta).isoformat() + "Z",
                    "count": len(values),
                    "sum": round(sum(values), 6),
                    "avg": round(sum(values) / len(values), 6) if values else 0.0,
                    "min": round(min(values), 6) if values else 0.0,
                    "max": round(max(values), 6) if values else 0.0,
                }
            )
        return result
