"""Small audit helpers: percentiles, LIKE escaping, export window and retention cutoff."""

EXPORT_ROLES = {"admin", "auditor"}


def percentile(values, pct):
    """Empty input returns None; a singleton returns that value (#1043)."""
    if not values:
        return None
    data = sorted(values)
    if len(data) == 1:
        return data[0]
    idx = round((pct / 100) * (len(data) - 1))
    return data[idx]


def escape_like(term, escape="\\"):
    """Escape %, _ and the escape char so search stays literal (#1044)."""
    for ch in (escape, "%", "_"):
        term = term.replace(ch, escape + ch)
    return f"%{term}%"


def filter_export(rows, role, start, end):
    """Only allowed roles may export, and only rows within [start, end] (#1045)."""
    if role not in EXPORT_ROLES:
        raise PermissionError("role not allowed to export audit events")
    if start > end:
        raise ValueError("invalid time range")
    return [r for r in rows if start <= r["timestamp"] <= end]


def prune(rows, cutoff):
    """Delete rows strictly older than cutoff; rows at cutoff are kept (#1046)."""
    return [r for r in rows if r["timestamp"] >= cutoff]
