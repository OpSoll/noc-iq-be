from prometheus_client import Histogram, Counter, generate_latest, CONTENT_TYPE_LATEST
from fastapi import APIRouter, Response

router = APIRouter()

DB_QUERY_DURATION = Histogram(
    "db_query_duration_seconds",
    "Database query duration in seconds",
    labelnames=["operation", "table"],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

DB_QUERY_TOTAL = Counter(
    "db_query_total",
    "Total number of database queries",
    labelnames=["operation", "table"],
)


def record_db_query(operation: str, table: str, duration_seconds: float) -> None:
    DB_QUERY_TOTAL.labels(operation=operation, table=table).inc()
    DB_QUERY_DURATION.labels(operation=operation, table=table).observe(duration_seconds)


def setup_db_metrics() -> None:
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    @event.listens_for(Engine, "before_cursor_execute")
    def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        import time
        context.info["query_start_time"] = time.perf_counter()
        op = statement.strip().split()[0].lower() if statement.strip() else "unknown"
        context.info["query_operation"] = op
        table = _extract_table_name(statement)
        context.info["query_table"] = table

    @event.listens_for(Engine, "after_cursor_execute")
    def _after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        import time
        start = context.info.get("query_start_time")
        if start is None:
            return
        duration = time.perf_counter() - start
        op = context.info.get("query_operation", "unknown")
        table = context.info.get("query_table", "unknown")
        record_db_query(op, table, duration)


def _extract_table_name(statement: str) -> str:
    import re
    lower = statement.lower().strip()
    for pattern in (
        r"(?:FROM|INTO|UPDATE|JOIN)\s+(?:\"?\w+\"?\.)?\"?(\w+)\"?",
    ):
        match = re.search(pattern, lower)
        if match:
            return match.group(1)
    return "unknown"


@router.get("/metrics")
def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
