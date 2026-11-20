"""Small observability helpers: migration coverage, route labels, 4xx classes, slow queries."""
import logging
import os
from collections import Counter

logger = logging.getLogger("noc_iq.slow_query")
SLOW_QUERY_MS = float(os.getenv("SLOW_QUERY_THRESHOLD_MS", "500"))


def missing_migrations(model_tables, migration_tables, exclusions=()):
    """Tables defined by models but never created in a migration (#914)."""
    return sorted(set(model_tables) - set(migration_tables) - set(exclusions))


def route_label(request):
    """Use the route template (bounded cardinality) not the raw path (#915)."""
    route = getattr(request, "scope", {}).get("route")
    return getattr(route, "path", None) or "unmatched"


ERROR_4XX = Counter()


def error_class(status):
    return {400: "bad_request", 401: "unauthorized", 403: "forbidden", 404: "not_found",
            409: "conflict", 422: "validation", 429: "rate_limited"}.get(status, "other_4xx")


def record_status(status):
    """Count only 4xx responses by normalized class (#916)."""
    if 400 <= status < 500:
        ERROR_4XX[error_class(status)] += 1


def log_slow_query(statement, duration_ms, threshold_ms=None):
    """Warn on slow queries; logs statement shape only, never parameters (#917)."""
    limit = SLOW_QUERY_MS if threshold_ms is None else threshold_ms
    if duration_ms < limit:
        return False
    logger.warning("slow query", extra={"duration_ms": round(duration_ms, 2), "statement": statement[:200]})
    return True
