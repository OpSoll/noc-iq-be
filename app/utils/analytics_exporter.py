"""Analytics export utilities for dashboard and reporting use cases."""
import csv
import io
import json
import time
from typing import Any, Dict, List

from app.models.sla import SLADashboardKPI, SLATrendPoint, SLAPerformanceAggregation

# ---------------------------------------------------------------------------
# Benchmark regression thresholds
# ---------------------------------------------------------------------------
# Maximum acceptable latency (milliseconds) for each export operation type.
# CI will fail if these are exceeded on the synthetic benchmark datasets.
AGGREGATION_LATENCY_THRESHOLD_MS: float = 200.0
EXPORT_LATENCY_THRESHOLD_MS: float = 500.0


def benchmark_export(fn, *args, **kwargs) -> dict[str, Any]:
    """
    Time a single export call and return a result dict with duration and
    threshold status.

    Args:
        fn: Callable export function to benchmark.
        *args / **kwargs: Forwarded to fn.

    Returns:
        dict with keys: result, duration_ms, within_threshold, threshold_ms
    """
    threshold_ms = kwargs.pop("_threshold_ms", EXPORT_LATENCY_THRESHOLD_MS)
    t0 = time.perf_counter()
    result = fn(*args, **kwargs)
    duration_ms = (time.perf_counter() - t0) * 1000.0
    return {
        "result": result,
        "duration_ms": round(duration_ms, 3),
        "threshold_ms": threshold_ms,
        "within_threshold": duration_ms <= threshold_ms,
    }


def export_dashboard_kpi(kpi: SLADashboardKPI, format: str = "json") -> Any:
    """Export dashboard KPI data in JSON or CSV format.
    
    Args:
        kpi: Dashboard KPI object
        format: Export format ('json' or 'csv')
        
    Returns:
        Exported data in specified format
    """
    format = format.lower()
    data = kpi.model_dump(mode="json")
    
    if format == "json":
        return data
    
    if format != "csv":
        raise ValueError("Unsupported export format. Use 'json' or 'csv'.")
    
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=data.keys())
    writer.writeheader()
    writer.writerow(data)
    return buffer.getvalue()


def export_trends(trends: list[SLATrendPoint], format: str = "json") -> Any:
    """Export trends data in JSON or CSV format.
    
    Args:
        trends: List of trend point objects
        format: Export format ('json' or 'csv')
        
    Returns:
        Exported data in specified format
    """
    format = format.lower()
    data = [trend.model_dump(mode="json") for trend in trends]
    
    if format == "json":
        return data
    
    if format != "csv":
        raise ValueError("Unsupported export format. Use 'json' or 'csv'.")
    
    if not data:
        # Handle empty dataset safely
        return "date,total_outages,violations,rewards,penalties\n"
    
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=data[0].keys())
    writer.writeheader()
    for row in data:
        writer.writerow(row)
    return buffer.getvalue()


def export_performance_aggregation(
    aggregation: SLAPerformanceAggregation, format: str = "json"
) -> Any:
    """Export performance aggregation data in JSON or CSV format.
    
    Args:
        aggregation: Performance aggregation object
        format: Export format ('json' or 'csv')
        
    Returns:
        Exported data in specified format
    """
    format = format.lower()
    data = aggregation.model_dump(mode="json")
    
    if format == "json":
        return data
    
    if format != "csv":
        raise ValueError("Unsupported export format. Use 'json' or 'csv'.")
    
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=data.keys())
    writer.writeheader()
    writer.writerow(data)
    return buffer.getvalue()


def export_analytics_summary(
    kpi: SLADashboardKPI,
    trends: list[SLATrendPoint],
    aggregation: SLAPerformanceAggregation | None = None,
    format: str = "json",
) -> Any:
    """Export comprehensive analytics summary combining KPI, trends, and optional aggregation.
    
    Args:
        kpi: Dashboard KPI object
        trends: List of trend point objects
        aggregation: Optional performance aggregation object
        format: Export format ('json' or 'csv')
        
    Returns:
        Exported data in specified format
    """
    format = format.lower()
    
    summary = {
        "kpi": kpi.model_dump(mode="json"),
        "trends": [trend.model_dump(mode="json") for trend in trends],
        "trend_count": len(trends),
    }
    
    if aggregation:
        summary["aggregation"] = aggregation.model_dump(mode="json")
    
    if format == "json":
        return summary
    
    if format != "csv":
        raise ValueError("Unsupported export format. Use 'json' or 'csv'.")
    
    # For CSV, export each section with headers
    buffer = io.StringIO()
    
    # KPI section
    buffer.write("# KPI Metrics\n")
    kpi_writer = csv.DictWriter(buffer, fieldnames=summary["kpi"].keys())
    kpi_writer.writeheader()
    kpi_writer.writerow(summary["kpi"])
    buffer.write("\n")
    
    # Trends section
    buffer.write("# Trends Data\n")
    if trends:
        trend_data = summary["trends"]
        trend_writer = csv.DictWriter(buffer, fieldnames=trend_data[0].keys())
        trend_writer.writeheader()
        for row in trend_data:
            trend_writer.writerow(row)
    else:
        buffer.write("date,total_outages,violations,rewards,penalties\n")
    buffer.write("\n")
    
    # Aggregation section (if available)
    if aggregation:
        buffer.write("# Performance Aggregation\n")
        agg_writer = csv.DictWriter(buffer, fieldnames=summary["aggregation"].keys())
        agg_writer.writeheader()
        agg_writer.writerow(summary["aggregation"])
    
    return buffer.getvalue()





class AnalyticsExporter:
    """Wraps row-level records in a self-documenting export container schema."""

    SCHEMA_VERSION = "1.2.0"

    FIELD_METADATA: Dict[str, Dict[str, str]] = {
        "export_timestamp": {
            "type": "ISO8601_DATETIME",
            "description": "UTC extraction timestamp",
        },
        "status_scope": {
            "type": "STRING",
            "description": "The processing state category of the transaction",
        },
        "transaction_count": {
            "type": "INTEGER",
            "description": "Total count of records processed within the window",
        },
        "aggregate_volume": {
            "type": "DECIMAL",
            "description": "Summed financial volume of transactions",
        },
    }

    def generate_stabilized_export(self, payload_data: List[Dict[str, Any]]) -> str:
        """Wraps row-level records within a formalized, self-documenting export container schema."""
        export_envelope: Dict[str, Any] = {
            "metadata": {
                "schema_version": self.SCHEMA_VERSION,
                "fields": self.FIELD_METADATA,
            },
            "data": payload_data,
        }
        return json.dumps(export_envelope, indent=2)
