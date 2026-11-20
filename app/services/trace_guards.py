"""Small trace helpers: stable timeline order, nested masking, Celery headers, safe traceparent."""
import re
import uuid

SECRET_KEYS = {"token", "password", "private_key", "secret", "access_token", "refresh_token"}
TRACEPARENT = re.compile(r"^00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$")
MAX_TRACE_HEADER = 512


def order_timeline(events):
    """Order by timestamp, then id, for deterministic output (#1047)."""
    return sorted(events, key=lambda e: (e["timestamp"], e["id"]))


def mask(value):
    """Recursively redact secret keys in nested dicts/lists (#1048)."""
    if isinstance(value, dict):
        return {k: "***" if k.lower() in SECRET_KEYS else mask(v) for k, v in value.items()}
    if isinstance(value, list):
        return [mask(v) for v in value]
    return value


def task_headers(trace_id=None, correlation_id=None):
    """Headers for Celery dispatch; generate fresh IDs when none (#1049)."""
    return {
        "trace_id": trace_id or uuid.uuid4().hex,
        "correlation_id": correlation_id or str(uuid.uuid4()),
    }


def parse_traceparent(header):
    """Return a valid traceparent or None; never raises (#1050)."""
    if not header or len(header) > MAX_TRACE_HEADER:
        return None
    value = header.strip().lower()
    return value if TRACEPARENT.match(value) else None
