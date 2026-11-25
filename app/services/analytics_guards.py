"""Small analytics helpers: rate keys, sparse buckets, export headers, sort guard."""
import re
from datetime import datetime, timedelta, timezone

RATE_KEY = re.compile(r"^[A-Z0-9]{1,12}$")
EXPORT_COLUMNS = ["period", "severity", "total", "met", "violated", "mttr_minutes"]
ALLOWED_SORT = {"period", "severity", "total", "mttr_minutes"}


def validate_rate_keys(rates):
    """Reject malformed asset rate keys, naming the offender (#1039)."""
    for key in rates:
        if not isinstance(key, str) or not RATE_KEY.match(key):
            raise ValueError(f"malformed asset rate key: {key!r}")
    return rates


def fill_buckets(points, start, end, step=timedelta(days=1)):
    """Return ordered UTC buckets with explicit zeros for gaps (#1040)."""
    start = start.astimezone(timezone.utc)
    end = end.astimezone(timezone.utc)
    by_key = {p[0].astimezone(timezone.utc): p[1] for p in points}
    out, cur = [], start
    while cur <= end:
        out.append((cur, by_key.get(cur, 0)))
        cur += step
    return out


def export_header(rows):
    """Header is the same whether rows is empty or not (#1041)."""
    return {"columns": list(EXPORT_COLUMNS), "rows": [[r.get(c) for c in EXPORT_COLUMNS] for r in rows]}


def validate_sort(field):
    """Return (field, tie-breaker) or raise for unsupported keys (#1042)."""
    name = field.lstrip("-")
    if name not in ALLOWED_SORT:
        raise ValueError({"loc": ["query", "sort"], "msg": f"unsupported sort field: {name}"})
    return field, "id"


def utc(y, m, d):
    return datetime(y, m, d, tzinfo=timezone.utc)
