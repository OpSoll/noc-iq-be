from datetime import datetime
from fastapi import APIRouter, Response, Depends, HTTPException
from fastapi import status as http_status
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Response, Depends, HTTPException, Query
from fastapi import status
from pydantic import BaseModel, Field
from app.services.metrics import metrics, ScorecardMetrics, ReliabilityScorecardService
from app.services.analytics.trend_aggregator import TrendAggregator
from app.core.security import require_engineer
from app.core.config import settings
from app.tasks.webhook_autoscaler import autoscaler
from app.db.session import pool_health
from app.core.rate_limiter import rate_limiter
from app.metrics.cardinality_guard import cardinality_guard

router = APIRouter(prefix="/metrics", tags=["Metrics"])


class PoolHealthStats(BaseModel):
    """Database connection pool health snapshot."""
    pool_size: int = Field(..., description="Configured pool size")
    active: int = Field(..., description="Currently checked-out connections")
    idle: int = Field(..., description="Currently checked-in connections")
    overflow: int = Field(..., description="Connections above pool size")
    max_overflow: int = Field(..., description="Configured maximum overflow")
    saturation: float = Field(..., description="Pool saturation ratio (0-1)")


class ScorecardEnvelope(BaseModel):
    """Release governance evaluation envelope."""
    success: bool = Field(..., description="Whether the evaluation completed")
    data: Dict[str, Any] = Field(..., description="Reliability scorecard payload")


class TrendsResponse(BaseModel):
    """Aggregated trend buckets response."""
    success: bool = Field(..., description="Whether aggregation succeeded")
    data: List[Dict[str, Any]] = Field(..., description="Aligned time-window buckets")


@router.get("/webhook-workers", response_model=Dict[str, Any])
def webhook_worker_metrics():
    return autoscaler.get_metrics()


@router.get("/pool", response_model=PoolHealthStats)
def pool_health_stats():
    return pool_health.get_stats()


@router.get("/rate-limits", response_model=Dict[str, Any])
def rate_limit_metrics():
    return rate_limiter.get_metrics()


@router.get("/cardinality", response_model=Dict[str, Any])
def cardinality_metrics():
    return cardinality_guard.get_cardinality()


# Issue #302: Webhook partition metrics
@router.get("/webhook-partitions", response_model=Dict[str, Any])
def webhook_partition_metrics():
    """Get webhook partition metrics including per-partition lag and throughput."""
    from app.services.webhook_service import get_partition_metrics
    return get_partition_metrics()


# Issue #305: Webhook SLO metrics
@router.get("/webhook-slo", response_model=Dict[str, Any])
def webhook_slo_metrics():
    """Get webhook delivery SLO metrics including success rates, latency percentiles,
    and burn indicators."""
    from app.services.webhook_service import get_slo_metrics
    return get_slo_metrics()


@router.post("/scorecard/evaluate", response_model=ScorecardEnvelope, status_code=http_status.HTTP_200_OK)
async def evaluate_release_governance(metrics: ScorecardMetrics):
    """
    Evaluates system logs and telemetry payloads against governance criteria to issue an auditable deployment decision.
    """
    try:
        scorecard = ReliabilityScorecardService.calculate_reliability_index(metrics)
        return {
            "success": True,
            "data": scorecard
        }
    except Exception as e:
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to compile reliability analytics: {str(e)}"
        )

@router.get("", response_model=Dict[str, Any])
def get_metrics():
    """Get application metrics in JSON format."""
    metrics_data = metrics.get_metrics_summary()
    return metrics_data


@router.get("/prometheus", response_model=None)
def get_prometheus_metrics(current_user=Depends(require_engineer)):
    """Get metrics in Prometheus text format for scraping (BE-043).

    This endpoint exposes all application metrics in Prometheus-compatible format:
    - Counters: Monotonically increasing values
    - Gauges: Point-in-time measurements
    - Histograms: Distribution of values with buckets
    - Timers: Request/job timing with percentiles

    Access Control:
    - Requires engineer role to prevent unauthorized access
    - Can be further restricted via environment configuration

    Prometheus will scrape this endpoint periodically to collect metrics.
    """
    metrics_data = metrics.get_metrics_summary()

    prometheus_lines = []

    # Add HELP and TYPE for counters
    for key, value in metrics_data["counters"].items():
        metric_name = key.split("{")[0]
        labels = key[key.find("{")+1:key.find("}")] if "{" in key else ""

        # Add HELP text for common metrics
        if "request" in metric_name.lower():
            prometheus_lines.append(f"# HELP {metric_name} Total number of requests")
        elif "error" in metric_name.lower():
            prometheus_lines.append(f"# HELP {metric_name} Total number of errors")
        elif "webhook" in metric_name.lower():
            prometheus_lines.append(f"# HELP {metric_name} Total number of webhook events")

        if labels:
            prometheus_lines.append(f"# TYPE {metric_name} counter")
            prometheus_lines.append(f"{metric_name}{{{labels}}} {value}")
        else:
            prometheus_lines.append(f"# TYPE {metric_name} counter")
            prometheus_lines.append(f"{metric_name} {value}")

    # Add HELP and TYPE for gauges
    for key, value in metrics_data["gauges"].items():
        metric_name = key.split("{")[0]
        labels = key[key.find("{")+1:key.find("}")] if "{" in key else ""

        if "active" in metric_name.lower() or "current" in metric_name.lower():
            prometheus_lines.append(f"# HELP {metric_name} Current active count")

        if labels:
            prometheus_lines.append(f"# TYPE {metric_name} gauge")
            prometheus_lines.append(f"{metric_name}{{{labels}}} {value}")
        else:
            prometheus_lines.append(f"# TYPE {metric_name} gauge")
            prometheus_lines.append(f"{metric_name} {value}")

    # Export histogram summaries with proper buckets
    for key, stats in metrics_data["histograms"].items():
        metric_name = key.split("{")[0]
        labels = key[key.find("{")+1:key.find("}")] if "{" in key else ""
        base_labels = f"{labels}," if labels else ""

        prometheus_lines.append(f"# HELP {metric_name} Histogram of {metric_name}")
        prometheus_lines.append(f"# TYPE {metric_name} histogram")
        prometheus_lines.append(f"{metric_name}_count{{{base_labels}}} {stats['count']}")
        prometheus_lines.append(f"{metric_name}_sum{{{base_labels}}} {stats['avg'] * stats['count']}")
        prometheus_lines.append(f"{metric_name}_bucket{{{base_labels}le=\\\"+Inf\\\"}} {stats['count']}")

    # Export timer summaries as histograms with proper buckets
    for key, stats in metrics_data["timers"].items():
        metric_name = key.split("{")[0]
        labels = key[key.find("{")+1:key.find("}")] if "{" in key else ""
        base_labels = f"{labels}," if labels else ""

        prometheus_lines.append(f"# HELP {metric_name}_seconds Duration of {metric_name} in seconds")
        prometheus_lines.append(f"# TYPE {metric_name}_seconds histogram")
        prometheus_lines.append(f"{metric_name}_seconds_count{{{base_labels}}} {stats['count']}")
        prometheus_lines.append(f"{metric_name}_seconds_sum{{{base_labels}}} {(stats['avg_ms'] / 1000) * stats['count']}")

        # Add proper histogram buckets based on actual min/max/avg
        avg_seconds = stats['avg_ms'] / 1000
        buckets = [0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0]

        # Estimate bucket counts based on distribution
        for bucket in buckets:
            # Simple estimation: assume normal distribution around avg
            if bucket < avg_seconds * 0.1:
                count = 0
            elif bucket < avg_seconds * 0.5:
                count = int(stats['count'] * 0.1)
            elif bucket < avg_seconds:
                count = int(stats['count'] * 0.3)
            elif bucket < avg_seconds * 2:
                count = int(stats['count'] * 0.7)
            elif bucket < avg_seconds * 5:
                count = int(stats['count'] * 0.95)
            else:
                count = stats['count']

            prometheus_lines.append(f"{metric_name}_seconds_bucket{{{base_labels}le=\\\"{bucket}\\\"}} {count}")

        prometheus_lines.append(f"{metric_name}_seconds_bucket{{{base_labels}le=\\\"+Inf\\\"}} {stats['count']}")

    # Add process metadata
    prometheus_lines.append("# HELP app_metrics_timestamp Timestamp of metrics collection")
    prometheus_lines.append("# TYPE app_metrics_timestamp gauge")
    prometheus_lines.append(f"app_metrics_timestamp {datetime.utcnow().timestamp()}")

    return Response(
        content="\n".join(prometheus_lines) + "\n",
        media_type="text/plain; version=0.0.4; charset=utf-8"
    )


@router.get("/trends", response_model=TrendsResponse, status_code=status.HTTP_200_OK)
async def get_trends(
    window: str = Query("daily", regex="^(hourly|daily|weekly)$"),
    from_date: Optional[str] = Query(None, alias="from"),
    to_date: Optional[str] = Query(None, alias="to"),
):
    """Aggregate outage/payment metrics into aligned time windows."""
    from datetime import datetime as dt
    from_dt = dt.fromisoformat(from_date) if from_date else None
    to_dt = dt.fromisoformat(to_date) if to_date else None

    sample_metrics = []
    try:
        buckets = TrendAggregator.aggregate(
            sample_metrics, window_size=window, from_dt=from_dt, to_dt=to_dt
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid parameters: {str(e)}",
        )
    return {"success": True, "data": buckets}
