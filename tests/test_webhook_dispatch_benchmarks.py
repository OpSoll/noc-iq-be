"""
Webhook dispatch benchmark suite — Optimizes event-to-webhook matching for large registries.

Runs repeatable performance benchmarks against webhook lookup operations and enforces
regression thresholds. A JSON artifact is written to tests/benchmark-results.json for
CI artifact retention and trend comparison.

Thresholds (configurable via env vars for CI tuning):
  WEBHOOK_LOOKUP_THRESHOLD_MS default 50 ms (for 10k webhooks)
"""
import json
import os
import time
import unittest
from pathlib import Path
from uuid import uuid4

from app.models.webhook import Webhook, WebhookEvent
from app.services.webhook_service import get_active_webhooks_for_event

# ---------------------------------------------------------------------------
# Threshold overrides from environment (allows CI to relax in slow runners)
# ---------------------------------------------------------------------------
_LOOKUP_THRESHOLD = float(
    os.environ.get("WEBHOOK_LOOKUP_THRESHOLD_MS", 50)
)

# ---------------------------------------------------------------------------
# Synthetic dataset sizes
# ---------------------------------------------------------------------------
WEBHOOK_COUNTS = [100, 1_000, 10_000]

# Path where JSON benchmark artifact is written
ARTIFACT_PATH = Path(__file__).parent / "webhook-benchmark-results.json"


# ---------------------------------------------------------------------------
# Benchmark helpers
# ---------------------------------------------------------------------------

def _time_fn(fn, *args, **kwargs) -> float:
    """Return wall-clock duration in ms."""
    t0 = time.perf_counter()
    fn(*args, **kwargs)
    return (time.perf_counter() - t0) * 1000.0


def create_test_webhooks(db, count: int, event_types: list[str]) -> list[Webhook]:
    """Create test webhooks with varying event subscriptions.
    
    Distributes webhooks across event types to simulate real-world usage.
    """
    webhooks = []
    for i in range(count):
        # Distribute events: 50% single event, 30% two events, 20% all events
        rand = i % 10
        if rand < 5:
            events = [event_types[rand % len(event_types)]]
        elif rand < 8:
            events = event_types[:2]
        else:
            events = event_types
        
        webhook = Webhook(
            name=f"benchmark-webhook-{i}",
            url=f"https://example.com/webhook/{i}",
            secret=f"secret-{i}",
            events=json.dumps(events),
            max_retries=3,
            is_active=True,
        )
        db.add(webhook)
        webhooks.append(webhook)
    
    db.commit()
    return webhooks


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

class WebhookDispatchBenchmarkSuite(unittest.TestCase):
    """
    Benchmark suite that validates webhook lookup latencies against thresholds.
    
    All results are collected in cls._results and written to the JSON
    artifact in tearDownClass so CI can retain and compare them over time.
    """

    _results: list[dict] = []

    @classmethod
    def tearDownClass(cls) -> None:
        """Write collected benchmark results to JSON artifact."""
        artifact = {
            "suite": "webhook_dispatch_benchmarks",
            "thresholds": {
                "lookup_ms": _LOOKUP_THRESHOLD,
            },
            "results": cls._results,
        }
        ARTIFACT_PATH.write_text(json.dumps(artifact, indent=2))

    def _record(self, name: str, duration_ms: float, threshold_ms: float, webhook_count: int | None = None):
        entry = {
            "benchmark": name,
            "duration_ms": round(duration_ms, 3),
            "threshold_ms": threshold_ms,
            "within_threshold": duration_ms <= threshold_ms,
        }
        if webhook_count is not None:
            entry["webhook_count"] = webhook_count
        self.__class__._results.append(entry)
        return entry

    # ------------------------------------------------------------------ #
    # Webhook lookup benchmarks — multiple dataset sizes                #
    # ------------------------------------------------------------------ #

    def test_webhook_lookup_latency_scales_with_registry_size(self, db):
        """get_active_webhooks_for_event scales efficiently with webhook count."""
        event_types = ["sla.violation", "sla.warning", "sla.resolved"]
        
        for count in WEBHOOK_COUNTS:
            # Clean up any existing webhooks
            db.query(Webhook).delete()
            db.commit()
            
            # Create test webhooks
            webhooks = create_test_webhooks(db, count, event_types)
            
            # Benchmark lookup for each event type
            for event_type in event_types:
                event = WebhookEvent(event_type)
                
                # Warm up cache
                get_active_webhooks_for_event(db, event)
                
                # Measure lookup time
                duration_ms = _time_fn(get_active_webhooks_for_event, db, event)
                entry = self._record(
                    f"webhook_lookup_{event_type.replace('.', '_')}",
                    duration_ms,
                    _LOOKUP_THRESHOLD,
                    webhook_count=count,
                )
                
                # Only enforce threshold for largest dataset
                if count == max(WEBHOOK_COUNTS):
                    self.assertTrue(
                        entry["within_threshold"],
                        f"Webhook lookup ({event_type}, {count} webhooks) took {duration_ms:.1f}ms, "
                        f"threshold={_LOOKUP_THRESHOLD}ms",
                    )
            
            # Clean up for next iteration
            db.query(Webhook).delete()
            db.commit()

    def test_webhook_lookup_correctness_preserved(self, db):
        """Optimized lookup returns correct webhooks without misrouting."""
        event_types = ["sla.violation", "sla.warning", "sla.resolved"]
        
        # Create webhooks with specific event subscriptions
        violation_webhook = Webhook(
            name="violation-only",
            url="https://example.com/violation",
            events=json.dumps(["sla.violation"]),
            is_active=True,
        )
        warning_webhook = Webhook(
            name="warning-only",
            url="https://example.com/warning",
            events=json.dumps(["sla.warning"]),
            is_active=True,
        )
        multi_webhook = Webhook(
            name="multi-event",
            url="https://example.com/multi",
            events=json.dumps(["sla.violation", "sla.warning"]),
            is_active=True,
        )
        inactive_webhook = Webhook(
            name="inactive",
            url="https://example.com/inactive",
            events=json.dumps(["sla.violation"]),
            is_active=False,
        )
        
        db.add_all([violation_webhook, warning_webhook, multi_webhook, inactive_webhook])
        db.commit()
        
        # Test violation event lookup
        violation_webhooks = get_active_webhooks_for_event(db, WebhookEvent.SLA_VIOLATION)
        violation_ids = {w.id for w in violation_webhooks}
        self.assertIn(violation_webhook.id, violation_ids)
        self.assertIn(multi_webhook.id, violation_ids)
        self.assertNotIn(warning_webhook.id, violation_ids)
        self.assertNotIn(inactive_webhook.id, violation_ids)
        
        # Test warning event lookup
        warning_webhooks = get_active_webhooks_for_event(db, WebhookEvent.SLA_WARNING)
        warning_ids = {w.id for w in warning_webhooks}
        self.assertIn(warning_webhook.id, warning_ids)
        self.assertIn(multi_webhook.id, warning_ids)
        self.assertNotIn(violation_webhook.id, warning_ids)
        self.assertNotIn(inactive_webhook.id, warning_ids)
        
        # Clean up
        db.query(Webhook).delete()
        db.commit()

    def test_benchmark_results_artifact_written(self):
        """After the suite runs, a JSON artifact exists at the expected path."""
        # Force tearDownClass to flush results (normally called by the runner)
        self.__class__.tearDownClass()
        self.assertTrue(ARTIFACT_PATH.exists(), f"Artifact not found at {ARTIFACT_PATH}")
        data = json.loads(ARTIFACT_PATH.read_text())
        self.assertIn("results", data)
        self.assertIsInstance(data["results"], list)


if __name__ == "__main__":
    unittest.main()
