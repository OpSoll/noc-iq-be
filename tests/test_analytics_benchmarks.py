"""
SLA/payment analytics benchmark suite — Issue #376 (BE-W5-115).

Runs repeatable performance benchmarks against representative synthetic
datasets and enforces regression thresholds.  A JSON artifact is written
to tests/benchmark-results.json for CI artifact retention and trend comparison.

Thresholds (configurable via env vars for CI tuning):
  AGGREGATION_LATENCY_THRESHOLD_MS  default 200 ms
  EXPORT_LATENCY_THRESHOLD_MS       default 500 ms
"""
import json
import os
import time
import unittest
from pathlib import Path

from app.models.sla import SLADashboardKPI, SLATrendPoint, SLAPerformanceAggregation
from app.utils.analytics_exporter import (
    AGGREGATION_LATENCY_THRESHOLD_MS,
    EXPORT_LATENCY_THRESHOLD_MS,
    benchmark_export,
    export_analytics_summary,
    export_dashboard_kpi,
    export_performance_aggregation,
    export_trends,
)

# ---------------------------------------------------------------------------
# Threshold overrides from environment (allows CI to relax in slow runners)
# ---------------------------------------------------------------------------
_AGG_THRESHOLD = float(
    os.environ.get("AGGREGATION_LATENCY_THRESHOLD_MS", AGGREGATION_LATENCY_THRESHOLD_MS)
)
_EXPORT_THRESHOLD = float(
    os.environ.get("EXPORT_LATENCY_THRESHOLD_MS", EXPORT_LATENCY_THRESHOLD_MS)
)

# ---------------------------------------------------------------------------
# Synthetic dataset sizes
# ---------------------------------------------------------------------------
DATASET_SIZES = [100, 1_000, 10_000]

# Path where JSON benchmark artifact is written
ARTIFACT_PATH = Path(__file__).parent / "benchmark-results.json"


# ---------------------------------------------------------------------------
# Factories for synthetic data
# ---------------------------------------------------------------------------

def make_kpi() -> SLADashboardKPI:
    return SLADashboardKPI(
        total_outages=500,
        total_violations=120,
        total_rewards=75_000.0,
        total_penalties=18_000.0,
        net_payout=57_000.0,
    )


def make_trends(n: int) -> list[SLATrendPoint]:
    return [
        SLATrendPoint(
            date=f"2026-01-{(i % 28) + 1:02d}",
            total_outages=i % 10,
            violations=i % 5,
            rewards=float(i * 100),
            penalties=float(i * 25),
        )
        for i in range(n)
    ]


def make_aggregation() -> SLAPerformanceAggregation:
    return SLAPerformanceAggregation(
        total_outages=800,
        violation_rate=0.24,
        avg_mttr=18.5,
        payout_sum=95_000.0,
    )


# ---------------------------------------------------------------------------
# Benchmark helpers
# ---------------------------------------------------------------------------

def _time_fn(fn, *args, **kwargs) -> float:
    """Return wall-clock duration in ms."""
    t0 = time.perf_counter()
    fn(*args, **kwargs)
    return (time.perf_counter() - t0) * 1000.0


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

class AnalyticsBenchmarkSuite(unittest.TestCase):
    """
    Benchmark suite that validates export latencies against thresholds.

    All results are collected in cls._results and written to the JSON
    artifact in tearDownClass so CI can retain and compare them over time.
    """

    _results: list[dict] = []

    @classmethod
    def tearDownClass(cls) -> None:
        """Write collected benchmark results to JSON artifact."""
        artifact = {
            "suite": "analytics_benchmarks",
            "thresholds": {
                "aggregation_ms": _AGG_THRESHOLD,
                "export_ms": _EXPORT_THRESHOLD,
            },
            "results": cls._results,
        }
        ARTIFACT_PATH.write_text(json.dumps(artifact, indent=2))

    def _record(self, name: str, duration_ms: float, threshold_ms: float, size: int | None = None):
        entry = {
            "benchmark": name,
            "duration_ms": round(duration_ms, 3),
            "threshold_ms": threshold_ms,
            "within_threshold": duration_ms <= threshold_ms,
        }
        if size is not None:
            entry["dataset_size"] = size
        self.__class__._results.append(entry)
        return entry

    # ------------------------------------------------------------------ #
    # KPI export benchmarks                                               #
    # ------------------------------------------------------------------ #

    def test_kpi_export_json_latency(self):
        """export_dashboard_kpi (JSON) finishes within export threshold."""
        kpi = make_kpi()
        duration_ms = _time_fn(export_dashboard_kpi, kpi, format="json")
        entry = self._record("kpi_export_json", duration_ms, _EXPORT_THRESHOLD)
        self.assertTrue(
            entry["within_threshold"],
            f"KPI JSON export took {duration_ms:.1f}ms, threshold={_EXPORT_THRESHOLD}ms",
        )

    def test_kpi_export_csv_latency(self):
        """export_dashboard_kpi (CSV) finishes within export threshold."""
        kpi = make_kpi()
        duration_ms = _time_fn(export_dashboard_kpi, kpi, format="csv")
        entry = self._record("kpi_export_csv", duration_ms, _EXPORT_THRESHOLD)
        self.assertTrue(
            entry["within_threshold"],
            f"KPI CSV export took {duration_ms:.1f}ms, threshold={_EXPORT_THRESHOLD}ms",
        )

    # ------------------------------------------------------------------ #
    # Trend export benchmarks — multiple dataset sizes                   #
    # ------------------------------------------------------------------ #

    def test_trends_export_json_latency(self):
        """export_trends (JSON) over 100/1k/10k points meets aggregation threshold."""
        for n in DATASET_SIZES:
            trends = make_trends(n)
            duration_ms = _time_fn(export_trends, trends, format="json")
            entry = self._record("trends_export_json", duration_ms, _AGG_THRESHOLD, size=n)
            self.assertTrue(
                entry["within_threshold"],
                f"Trends JSON export ({n} pts) took {duration_ms:.1f}ms, "
                f"threshold={_AGG_THRESHOLD}ms",
            )

    def test_trends_export_csv_latency(self):
        """export_trends (CSV) over 100/1k/10k points meets export threshold."""
        for n in DATASET_SIZES:
            trends = make_trends(n)
            duration_ms = _time_fn(export_trends, trends, format="csv")
            entry = self._record("trends_export_csv", duration_ms, _EXPORT_THRESHOLD, size=n)
            self.assertTrue(
                entry["within_threshold"],
                f"Trends CSV export ({n} pts) took {duration_ms:.1f}ms, "
                f"threshold={_EXPORT_THRESHOLD}ms",
            )

    # ------------------------------------------------------------------ #
    # Performance aggregation benchmarks                                  #
    # ------------------------------------------------------------------ #

    def test_aggregation_export_json_latency(self):
        """export_performance_aggregation (JSON) finishes within aggregation threshold."""
        agg = make_aggregation()
        duration_ms = _time_fn(export_performance_aggregation, agg, format="json")
        entry = self._record("aggregation_export_json", duration_ms, _AGG_THRESHOLD)
        self.assertTrue(
            entry["within_threshold"],
            f"Aggregation JSON export took {duration_ms:.1f}ms, threshold={_AGG_THRESHOLD}ms",
        )

    # ------------------------------------------------------------------ #
    # Full summary (composite) benchmark                                  #
    # ------------------------------------------------------------------ #

    def test_analytics_summary_export_latency(self):
        """export_analytics_summary (JSON + CSV) with 1k trends meets export threshold."""
        kpi = make_kpi()
        trends = make_trends(1_000)
        agg = make_aggregation()

        for fmt in ("json", "csv"):
            duration_ms = _time_fn(export_analytics_summary, kpi, trends, agg, format=fmt)
            entry = self._record(
                f"analytics_summary_{fmt}", duration_ms, _EXPORT_THRESHOLD, size=1_000
            )
            self.assertTrue(
                entry["within_threshold"],
                f"Analytics summary {fmt.upper()} export took {duration_ms:.1f}ms, "
                f"threshold={_EXPORT_THRESHOLD}ms",
            )

    # ------------------------------------------------------------------ #
    # benchmark_export() helper smoke-test                                #
    # ------------------------------------------------------------------ #

    def test_benchmark_export_helper_returns_within_threshold(self):
        """benchmark_export() helper reports correct within_threshold status."""
        kpi = make_kpi()
        bm = benchmark_export(
            export_dashboard_kpi, kpi, format="json",
            _threshold_ms=_EXPORT_THRESHOLD,
        )
        self.assertIn("duration_ms", bm)
        self.assertIn("within_threshold", bm)
        self.assertIn("result", bm)
        self.assertTrue(
            bm["within_threshold"],
            f"benchmark_export helper: {bm['duration_ms']}ms > {bm['threshold_ms']}ms",
        )

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
