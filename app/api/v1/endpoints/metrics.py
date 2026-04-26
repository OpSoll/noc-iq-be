from fastapi import APIRouter, Response
from app.services.metrics import metrics

router = APIRouter(prefix="/metrics", tags=["Metrics"])


@router.get("")
def get_metrics():
    """Get application metrics in JSON format."""
    metrics_data = metrics.get_metrics_summary()
    return metrics_data


@router.get("/prometheus")
def get_prometheus_metrics():
    """Get metrics in Prometheus text format for scraping."""
    metrics_data = metrics.get_metrics_summary()
    
    prometheus_lines = []
    
    # Export counters
    for key, value in metrics_data["counters"].items():
        metric_name = key.split("{")[0]
        labels = key[key.find("{")+1:key.find("}")] if "{" in key else ""
        if labels:
            prometheus_lines.append(f"# TYPE {metric_name} counter")
            prometheus_lines.append(f"{metric_name}{{{labels}}} {value}")
        else:
            prometheus_lines.append(f"# TYPE {metric_name} counter")
            prometheus_lines.append(f"{metric_name} {value}")
    
    # Export gauges
    for key, value in metrics_data["gauges"].items():
        metric_name = key.split("{")[0]
        labels = key[key.find("{")+1:key.find("}")] if "{" in key else ""
        if labels:
            prometheus_lines.append(f"# TYPE {metric_name} gauge")
            prometheus_lines.append(f"{metric_name}{{{labels}}} {value}")
        else:
            prometheus_lines.append(f"# TYPE {metric_name} gauge")
            prometheus_lines.append(f"{metric_name} {value}")
    
    # Export histogram summaries
    for key, stats in metrics_data["histograms"].items():
        metric_name = key.split("{")[0]
        labels = key[key.find("{")+1:key.find("}")] if "{" in key else ""
        base_labels = f"{labels}," if labels else ""
        
        prometheus_lines.append(f"# TYPE {metric_name} histogram")
        prometheus_lines.append(f"{metric_name}_count{{{base_labels}}} {stats['count']}")
        prometheus_lines.append(f"{metric_name}_sum{{{base_labels}}} {stats['avg'] * stats['count']}")
        prometheus_lines.append(f"{metric_name}_bucket{{{base_labels}le=\"+Inf\"}} {stats['count']}")
    
    # Export timer summaries as histograms
    for key, stats in metrics_data["timers"].items():
        metric_name = key.split("{")[0]
        labels = key[key.find("{")+1:key.find("}")] if "{" in key else ""
        base_labels = f"{labels}," if labels else ""
        
        prometheus_lines.append(f"# TYPE {metric_name}_seconds histogram")
        prometheus_lines.append(f"{metric_name}_seconds_count{{{base_labels}}} {stats['count']}")
        prometheus_lines.append(f"{metric_name}_seconds_sum{{{base_labels}}} {(stats['avg_ms'] / 1000) * stats['count']}")
        
        # Add some buckets for the histogram
        buckets = [0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 10.0, 30.0, 60.0]
        for bucket in buckets:
            count = sum(1 for t in [stats['min_ms'], stats['avg_ms'], stats['max_ms']] if t/1000 <= bucket)
            prometheus_lines.append(f"{metric_name}_seconds_bucket{{{base_labels}le=\"{bucket}\"}} {count}")
        prometheus_lines.append(f"{metric_name}_seconds_bucket{{{base_labels}le=\"+Inf\"}} {stats['count']}")
    
    return Response(
        content="\n".join(prometheus_lines),
        media_type="text/plain; version=0.0.4; charset=utf-8"
    )
