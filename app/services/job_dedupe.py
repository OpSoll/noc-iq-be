"""Job deduplication keys for SLA and webhook tasks (#307)."""

import hashlib
import json
from typing import Optional


def compute_dedupe_key(job_type: str, payload: dict) -> str:
    """Compute a deterministic deduplication key from job type and business identifiers."""
    if job_type == "sla_computation":
        device_id = payload.get("device_id", "")
        period = payload.get("period", "")
        base = f"sla:{device_id}:{period}"
    elif job_type == "webhook_dispatch":
        delivery_id = payload.get("delivery_id", "")
        base = f"webhook:{delivery_id}"
    else:
        base = json.dumps(payload, sort_keys=True)
    return hashlib.sha256(base.encode()).hexdigest()[:16]


DEDUPE_REGISTRY: dict = {}  # In production: use Redis with TTL


def is_duplicate(dedupe_key: str) -> bool:
    return dedupe_key in DEDUPE_REGISTRY


def register_dedupe_key(dedupe_key: str) -> None:
    DEDUPE_REGISTRY[dedupe_key] = True
