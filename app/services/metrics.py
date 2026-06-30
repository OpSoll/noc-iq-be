import time
import threading
from collections import defaultdict, deque
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field


@dataclass
class MetricPoint:
    timestamp: datetime
    value: float
    tags: Dict[str, str] = field(default_factory=dict)


class MetricsRegistry:
    """Thread-safe metrics registry for collecting and exposing application metrics."""
    
    def __init__(self):
        self._lock = threading.RLock()
        self._counters: Dict[str, float] = defaultdict(float)
        self._gauges: Dict[str, float] = {}
        self._histograms: Dict[str, deque] = defaultdict(lambda: deque(maxlen=1000))
        self._timers: Dict[str, List[float]] = defaultdict(list)
    
    def increment_counter(self, name: str, value: float = 1.0, tags: Dict[str, str] = None):
        """Increment a counter metric."""
        with self._lock:
            key = self._make_key(name, tags)
            self._counters[key] += value
    
    def set_gauge(self, name: str, value: float, tags: Dict[str, str] = None):
        """Set a gauge metric value."""
        with self._lock:
            key = self._make_key(name, tags)
            self._gauges[key] = value
    
    def record_histogram(self, name: str, value: float, tags: Dict[str, str] = None):
        """Record a histogram value."""
        with self._lock:
            key = self._make_key(name, tags)
            self._histograms[key].append(MetricPoint(datetime.utcnow(), value, tags or {}))
    
    def record_timer(self, name: str, duration_ms: float, tags: Dict[str, str] = None):
        """Record a timing measurement."""
        with self._lock:
            key = self._make_key(name, tags)
            self._timers[key].append(duration_ms)
            # Keep only last 1000 measurements per timer
            if len(self._timers[key]) > 1000:
                self._timers[key] = self._timers[key][-1000:]
    
    def _make_key(self, name: str, tags: Dict[str, str] = None) -> str:
        """Create a unique key for a metric with optional tags."""
        if not tags:
            return name
        tag_str = ",".join(f"{k}={v}" for k, v in sorted(tags.items()))
        return f"{name}{{{tag_str}}}"
    
    def get_metrics_summary(self) -> Dict[str, Any]:
        """Get a summary of all metrics for exposure."""
        with self._lock:
            summary = {
                "timestamp": datetime.utcnow().isoformat(),
                "counters": dict(self._counters),
                "gauges": dict(self._gauges),
                "histograms": {},
                "timers": {}
            }
            
            # Summarize histograms
            for key, points in self._histograms.items():
                if points:
                    values = [p.value for p in points]
                    summary["histograms"][key] = {
                        "count": len(values),
                        "min": min(values),
                        "max": max(values),
                        "avg": sum(values) / len(values),
                        "latest": points[-1].timestamp.isoformat()
                    }
            
            # Summarize timers
            for key, timings in self._timers.items():
                if timings:
                    summary["timers"][key] = {
                        "count": len(timings),
                        "min_ms": min(timings),
                        "max_ms": max(timings),
                        "avg_ms": sum(timings) / len(timings),
                        "p95_ms": self._percentile(timings, 95),
                        "p99_ms": self._percentile(timings, 99)
                    }
            
            return summary
    
    def _percentile(self, values: List[float], percentile: float) -> float:
        """Calculate percentile of values using linear interpolation.
        
        This robust formula handles sparse datasets (insufficient sample sizes)
        by returning 0.0 for empty arrays and the exact value for single-item arrays.
        For larger outlier-heavy distributions, it uses standard linear interpolation
        (similar to numpy.percentile with method='linear') to provide a deterministic
        and statistically sound metric regardless of sample pathology.
        """
        if not values:
            return 0.0
        n = len(values)
        if n == 1:
            return values[0]
            
        sorted_values = sorted(values)
        
        # Position is 0-indexed. Using formula: p * (n - 1)
        k = (percentile / 100.0) * (n - 1)
        f = int(k)
        c = f + 1
        
        if c >= n:
            return sorted_values[-1]
            
        # Linear interpolation between f and c
        d0 = sorted_values[f]
        d1 = sorted_values[c]
        return d0 + (d1 - d0) * (k - f)


# Global metrics registry instance
metrics = MetricsRegistry()


class TimerContext:
    """Context manager for timing operations."""
    
    def __init__(self, name: str, tags: Dict[str, str] = None):
        self.name = name
        self.tags = tags
        self.start_time = None
    
    def __enter__(self):
        self.start_time = time.time()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.start_time is not None:
            duration_ms = (time.time() - self.start_time) * 1000
            metrics.record_timer(self.name, duration_ms, self.tags)


def timer(name: str, tags: Dict[str, str] = None) -> TimerContext:
    """Create a timer context manager."""
    return TimerContext(name, tags)


def increment_counter(name: str, value: float = 1.0, tags: Dict[str, str] = None):
    """Increment a counter metric."""
    metrics.increment_counter(name, value, tags)


def set_gauge(name: str, value: float, tags: Dict[str, str] = None):
    """Set a gauge metric value."""
    metrics.set_gauge(name, value, tags)


def record_histogram(name: str, value: float, tags: Dict[str, str] = None):
    """Record a histogram value."""
    metrics.record_histogram(name, value, tags)


def record_retry_class_distribution(retry_class: str, tags: Dict[str, str] = None):
    metrics.increment_counter("retry_class_distribution", tags={**(tags or {}), "retry_class": retry_class})
def set_dead_letter_gauge(count: int):
    """Set the dead-letter payment queue gauge metric."""
    metrics.set_gauge("dead_letter_payments", float(count))


class MetricsLineageService:
    def __init__(self, db_session: Any = None):
        self.outage_repo = OutageRepository(db_session)
        self.sla_repo = SlaRepository(db_session)

    def _generate_lineage_trace(self, outage_id: str, sla_id: str, timestamp: str) -> str:
        """
        Generates a non-reversible, completely unique hash to act as a 
        trace reference without leaking tenant or internal infrastructure context.
        """
        salt_base = f"{outage_id}::{sla_id}::{timestamp}"
        return hashlib.sha256(salt_base.encode('utf-8')).hexdigest()[:16]

    def compile_sla_outage_analytics(self, start_time: datetime, end_time: datetime) -> List[Dict[str, Any]]:
        """
        Aggregates operational state metrics and appends strict audit-ready lineage markers.
        """
        outages = self.outage_repo.get_raw_outage_events(start_time, end_time)
        slas = self.sla_repo.get_active_sla_targets()
        
        compiled_analytics_rows = []
        
        # Cross-reference records to build trace lineages
        for outage in outages:
            for sla in slas:
                trace_id = self._generate_lineage_trace(
                    outage["outage_id"], 
                    sla["sla_contract_id"], 
                    outage["timestamp"]
                )
                
                # Lineage row structure completely strips PII or user indicators
                analytics_row = {
                    "metric_name": "sla_breach_incident",
                    "window_start": start_time.isoformat(),
                    "window_end": end_time.isoformat(),
                    "calculated_value": outage["duration_seconds"],
                    # Trace identifiers used directly inside investigation workflows
                    "lineage_metadata": {
                        "lineage_trace_id": f"trc-{trace_id}",
                        "source_outage_id": outage["outage_id"],
                        "source_sla_contract_id": sla["sla_contract_id"]
                    }
                }
                compiled_analytics_rows.append(analytics_row)
                
        return compiled_analytics_rows