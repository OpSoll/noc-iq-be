from prometheus_client import Counter, Histogram

webhook_dispatches_total = Counter(
    "webhook_dispatches_total",
    "Total number of webhook dispatches",
    ["event", "status_code"],
)

webhook_dispatch_duration_seconds = Histogram(
    "webhook_dispatch_duration_seconds",
    "Webhook dispatch duration in seconds",
    ["event"],
)