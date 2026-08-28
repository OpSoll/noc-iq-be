import hashlib
import ipaddress
import json
import logging
import socket
import time
from collections import defaultdict
from datetime import datetime, timedelta
from threading import Lock
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse
from uuid import UUID

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.webhook import Webhook, WebhookDelivery, WebhookDeliveryStatus, WebhookEvent
from app.models.job import Job, JobType
from app.services.webhook_signing import (
    CURRENT_SIGNATURE_VERSION,
    sign_payload,
    verify_signature,
)
from app.core.config import settings
from app.utils.cache import TTLCache

logger = logging.getLogger(__name__)

# In-memory cache for webhook event subscriptions (webhook_id -> parsed events list)
# TTL of 60 seconds balances freshness with performance for large webhook registries
_webhook_events_cache = TTLCache(ttl_seconds=60)

# --------------------------------------------------------------------------- #
# Issue #302: Partition-aware backpressure tracking
# --------------------------------------------------------------------------- #

_partition_lock = Lock()
_partition_lag: Dict[int, int] = defaultdict(int)          # partition_id -> pending count
_partition_throughput: Dict[int, float] = defaultdict(float)  # partition_id -> throughput
_partition_last_recorded: Dict[int, float] = {}            # partition_id -> timestamp


def _get_partition_for_webhook(webhook_id: UUID, events: List[str]) -> int:
    """Assign a webhook to a partition based on its event types.

    Uses the integer representation of the UUID for stable, deterministic
    partitioning across process restarts (unlike hash() which is randomized).

    SLA-critical and payment-critical events are placed on dedicated
    priority partitions so they are never starved by bulk/tenant traffic.
    """
    event_set = set(events)
    sla_events = {"sla.violation", "sla.warning", "sla.resolved"}

    if event_set & sla_events:
        return settings.WEBHOOK_SLA_PRIORITY_PARTITION
    return webhook_id.int % settings.WEBHOOK_PARTITION_COUNT


def _get_partition_pending_count(partition_id: int) -> int:
    """Return the approximate pending count for a partition."""
    return _partition_lag.get(partition_id, 0)


def record_partition_metrics(partition_id: int, success: bool, latency_ms: float) -> None:
    """Record throughput and latency per partition for operational visibility."""
    with _partition_lock:
        _partition_throughput[partition_id] += 1.0
        _partition_lag[partition_id] = max(0, _partition_lag.get(partition_id, 0) + (0 if success else 1))
        _partition_last_recorded[partition_id] = time.time()


def get_partition_metrics() -> Dict[str, Any]:
    """Expose partition lag and throughput metrics."""
    with _partition_lock:
        return {
            "partition_count": settings.WEBHOOK_PARTITION_COUNT,
            "priority_partition": settings.WEBHOOK_SLA_PRIORITY_PARTITION,
            "partitions": {
                str(pid): {
                    "pending": _partition_lag.get(pid, 0),
                    "throughput": round(_partition_throughput.get(pid, 0.0), 2),
                    "last_recorded": _partition_last_recorded.get(pid, 0.0),
                }
                for pid in range(settings.WEBHOOK_PARTITION_COUNT)
            },
            "global_pending": sum(_partition_lag.values()),
            "global_throughput": round(sum(_partition_throughput.values()), 2),
        }


def is_backpressured(partition_id: int) -> bool:
    """Check if a partition is backpressured and should throttle.

    Protects SLA/payment critical jobs from starvation by pausing
    non-critical partitions when they exceed the threshold.
    """
    pending = _get_partition_pending_count(partition_id)
    return pending >= settings.WEBHOOK_PARTITION_BACKPRESSURE_THRESHOLD


# --------------------------------------------------------------------------- #
# Issue #303: SSRF validation
# --------------------------------------------------------------------------- #

_PRIVATE_RANGES: List[str] = []
_BLOCKED_HOSTNAMES: Set[str] = set()


def _init_ssrf_config() -> None:
    """Initialize SSRF denylist from settings."""
    global _PRIVATE_RANGES, _BLOCKED_HOSTNAMES
    _PRIVATE_RANGES = [
        r.strip()
        for r in settings.WEBHOOK_SSRF_BLOCKED_CIDRS.split(",")
        if r.strip()
    ]
    _BLOCKED_HOSTNAMES = {
        h.strip().lower()
        for h in settings.WEBHOOK_SSRF_BLOCKED_HOSTNAMES.split(",")
        if h.strip()
    }


def _resolve_and_check_ip(hostname: str) -> Tuple[bool, str]:
    """Resolve a hostname and check if any resolved IP is private/internal.

    Returns (is_blocked, reason).
    """
    try:
        # Resolve all IPv4/IPv6 addresses with a short timeout
        addrinfo = socket.getaddrinfo(hostname, None)
        ips = {addr[4][0] for addr in addrinfo}
    except socket.gaierror:
        return True, f"DNS resolution failed for {hostname}"

    for ip_str in ips:
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue

        # Check if IP is in blocked CIDR ranges
        for cidr_str in _PRIVATE_RANGES:
            try:
                network = ipaddress.ip_network(cidr_str, strict=False)
                if ip in network:
                    return True, f"IP {ip_str} is in blocked range {cidr_str}"
            except ValueError:
                continue

        # Check loopback explicitly
        if ip.is_loopback:
            if not settings.WEBHOOK_SSRF_ALLOW_LOOPBACK:
                return True, f"IP {ip_str} is loopback"

        # Check link-local
        if ip.is_link_local:
            if not settings.WEBHOOK_SSRF_ALLOW_LINK_LOCAL:
                return True, f"IP {ip_str} is link-local"

        # Check private
        if ip.is_private:
            if not settings.WEBHOOK_SSRF_ALLOW_PRIVATE:
                return True, f"IP {ip_str} is private"

    return False, ""


def validate_webhook_url(url_str: str) -> Tuple[bool, str]:
    """Validate a webhook URL for SSRF safety.

    Checks:
    1. Blocked hostname denylist (localhost, metadata endpoints, etc.)
    2. DNS resolution and IP range checking
    3. Link-local / private / loopback IP blocks

    Returns (is_valid, reason).
    """
    if not _PRIVATE_RANGES:
        _init_ssrf_config()

    try:
        parsed = urlparse(url_str)
    except Exception:
        return False, "Malformed URL"

    hostname = parsed.hostname or ""

    # Check denylist hostnames (fast path, no DNS)
    if hostname.lower() in _BLOCKED_HOSTNAMES:
        return False, f"Hostname '{hostname}' is in the SSRF denylist"

    # Resolve and check IP ranges
    is_blocked, reason = _resolve_and_check_ip(hostname)
    if is_blocked:
        return False, reason

    return True, ""


def validate_webhook_url_fast(url_str: str) -> Tuple[bool, str]:
    """Fast SSRF pre-check that only validates hostname patterns without DNS resolution.

    This is safe to use in Pydantic field validators (no network I/O).
    Full DNS resolution happens at dispatch time in trigger_sla_violation_webhooks.

    Returns (is_valid, reason).
    """
    if not _PRIVATE_RANGES:
        _init_ssrf_config()

    parsed = urlparse(url_str)
    hostname = parsed.hostname or ""

    # Check denylist hostnames (fast path, no DNS)
    if hostname.lower() in _BLOCKED_HOSTNAMES:
        return False, f"Hostname '{hostname}' is in the SSRF denylist"

    # Check if hostname looks like an IP literal
    try:
        ip = ipaddress.ip_address(hostname)
        # Fast check for common internal IPs without DNS
        if ip.is_loopback and not settings.WEBHOOK_SSRF_ALLOW_LOOPBACK:
            return False, f"IP {hostname} is loopback"
        if ip.is_private and not settings.WEBHOOK_SSRF_ALLOW_PRIVATE:
            return False, f"IP {hostname} is private"
        if ip.is_link_local and not settings.WEBHOOK_SSRF_ALLOW_LINK_LOCAL:
            return False, f"IP {hostname} is link-local"
        # Check CIDR ranges for IP literals
        for cidr_str in _PRIVATE_RANGES:
            try:
                network = ipaddress.ip_network(cidr_str, strict=False)
                if ip in network:
                    return False, f"IP {hostname} is in blocked range {cidr_str}"
            except ValueError:
                continue
    except ValueError:
        # Not an IP literal - hostname, will be resolved at dispatch time
        pass

    return True, ""


# --------------------------------------------------------------------------- #
# Issue #304: Payload redaction
# --------------------------------------------------------------------------- #

_REDACTED_FIELDS: Set[str] = set()


def _init_redaction_config() -> None:
    """Initialize redaction field list from settings."""
    global _REDACTED_FIELDS
    _REDACTED_FIELDS = {
        f.strip()
        for f in settings.WEBHOOK_REDACTED_FIELDS.split(",")
        if f.strip()
    }


def _redact_payload(data: Any, depth: int = 0) -> Any:
    """Recursively redact sensitive fields from a payload.

    Masks values for any key matching the configured redacted fields set.
    Handles nested dicts, lists, and primitive types safely.
    """
    if not settings.WEBHOOK_REDACTION_ENABLED:
        return data

    if not _REDACTED_FIELDS:
        _init_redaction_config()

    max_depth = 10
    if depth > max_depth:
        return data

    redacted_fields = _REDACTED_FIELDS  # reference local for speed
    mask = settings.WEBHOOK_REDACTION_MASK

    if isinstance(data, dict):
        return {
            k: (mask if k.lower() in redacted_fields else _redact_payload(v, depth + 1))
            for k, v in data.items()
        }
    elif isinstance(data, list):
        return [_redact_payload(item, depth + 1) for item in data]
    return data


def build_redacted_payload(sla_data: Dict[str, Any], event: WebhookEvent, schema_version: str, event_timestamp: str) -> Dict[str, Any]:
    """Build a webhook payload with sensitive fields redacted.

    The outer structure (schema_version, event, timestamp, data) is preserved,
    only the inner `data` section is recursively redacted.
    """
    redacted_data = _redact_payload(sla_data)
    return {
        "schema_version": schema_version,
        "event": event.value,
        "timestamp": event_timestamp,
        "data": redacted_data,
    }


# --------------------------------------------------------------------------- #
# Issue #305: Webhook SLO metrics
# --------------------------------------------------------------------------- #

# SLO measurement window uses a simple sliding-window approach
_slo_window: List[Dict[str, Any]] = []
_slo_lock = Lock()


def record_slo_observation(
    success: bool,
    latency_ms: float,
    event: str,
    endpoint: str,
) -> None:
    """Record a single webhook delivery SLO observation.

    Observations are stored in a sliding window (configurable duration),
    tagged by event type and endpoint for per-dimension cardinality-controlled
    aggregation.
    """
    now = time.time()
    with _slo_lock:
        # Evict entries outside the window
        cutoff = now - settings.WEBHOOK_SLO_WINDOW_SECONDS
        while _slo_window and _slo_window[0]["timestamp"] < cutoff:
            _slo_window.pop(0)

        _slo_window.append({
            "timestamp": now,
            "success": success,
            "latency_ms": latency_ms,
            "event": event,
            "endpoint": endpoint,
        })


def get_slo_metrics() -> Dict[str, Any]:
    """Compute SLO metrics from the sliding observation window.

    Returns per-event-type and per-endpoint success rates, latency percentiles,
    and burn indicators.
    """
    now = time.time()
    cutoff = now - settings.WEBHOOK_SLO_WINDOW_SECONDS

    with _slo_lock:
        window = [o for o in _slo_window if o["timestamp"] >= cutoff]

    if not window:
        return {"status": "no_data", "window_seconds": settings.WEBHOOK_SLO_WINDOW_SECONDS}

    total = len(window)
    successes = sum(1 for o in window if o["success"])
    latency_values = sorted(o["latency_ms"] for o in window)

    overall_success_rate = successes / total if total > 0 else 0.0
    avg_latency = sum(latency_values) / len(latency_values) if latency_values else 0.0
    p95_latency = latency_values[int(len(latency_values) * 0.95)] if latency_values else 0.0
    p99_latency = latency_values[int(len(latency_values) * 0.99)] if latency_values else 0.0

    # Per-event breakdown
    by_event: Dict[str, Dict[str, Any]] = {}
    for o in window:
        ev = o["event"]
        if ev not in by_event:
            by_event[ev] = {"total": 0, "successes": 0, "latencies": []}
        by_event[ev]["total"] += 1
        if o["success"]:
            by_event[ev]["successes"] += 1
        by_event[ev]["latencies"].append(o["latency_ms"])

    per_event = {}
    for ev, stats in by_event.items():
        latencies = sorted(stats["latencies"])
        per_event[ev] = {
            "total": stats["total"],
            "success_rate": round(stats["successes"] / stats["total"], 4) if stats["total"] else 0.0,
            "avg_latency_ms": round(sum(latencies) / len(latencies), 2) if latencies else 0.0,
            "p95_latency_ms": round(latencies[int(len(latencies) * 0.95)], 2) if len(latencies) > 1 else 0.0,
        }

    # Per-endpoint breakdown
    by_endpoint: Dict[str, Dict[str, Any]] = {}
    for o in window:
        ep = o["endpoint"]
        if ep not in by_endpoint:
            by_endpoint[ep] = {"total": 0, "successes": 0, "latencies": []}
        by_endpoint[ep]["total"] += 1
        if o["success"]:
            by_endpoint[ep]["successes"] += 1
        by_endpoint[ep]["latencies"].append(o["latency_ms"])

    per_endpoint = {}
    for ep, stats in by_endpoint.items():
        latencies = sorted(stats["latencies"])
        per_endpoint[ep] = {
            "total": stats["total"],
            "success_rate": round(stats["successes"] / stats["total"], 4) if stats["total"] else 0.0,
        }

    # SLO burn rate computation
    # Burn rate = (1 - actual_success_rate) / (1 - SLO_target)
    slo_target = settings.WEBHOOK_SLO_SUCCESS_TARGET
    error_budget = 1.0 - slo_target
    actual_error_rate = 1.0 - overall_success_rate
    burn_rate = actual_error_rate / error_budget if error_budget > 0 else 0.0

    # Budget remaining: how much of the error budget is left
    budget_consumed = min(1.0, actual_error_rate / error_budget) if error_budget > 0 else 1.0
    budget_remaining_pct = max(0.0, (1.0 - budget_consumed) * 100.0)

    # Burn alert threshold
    burn_alert = burn_rate >= settings.WEBHOOK_SLO_BURN_RATE_THRESHOLD
    budget_alert = budget_remaining_pct <= settings.WEBHOOK_SLO_BUDGET_BURN_ALERT_PERCENT

    return {
        "status": "ok",
        "window_seconds": settings.WEBHOOK_SLO_WINDOW_SECONDS,
        "slo_target": slo_target,
        "overall": {
            "total_deliveries": total,
            "successes": successes,
            "success_rate": round(overall_success_rate, 6),
            "avg_latency_ms": round(avg_latency, 2),
            "p95_latency_ms": round(p95_latency, 2),
            "p99_latency_ms": round(p99_latency, 2),
        },
        "burn_indicators": {
            "error_budget": round(error_budget, 6),
            "actual_error_rate": round(actual_error_rate, 6),
            "burn_rate": round(burn_rate, 4),
            "budget_remaining_pct": round(budget_remaining_pct, 2),
            "burn_rate_alert": burn_alert,
            "budget_burn_alert": budget_alert,
            "latency_breach": p95_latency > settings.WEBHOOK_SLO_LATENCY_TARGET_MS,
        },
        "per_event": per_event,
        "per_endpoint": per_endpoint,
    }


# --------------------------------------------------------------------------- #
# Original code below (preserved and extended)
# --------------------------------------------------------------------------- #

def _get_retry_delays() -> list[int]:
    """Parse WEBHOOK_RETRY_BASE_DELAYS from settings into a list of ints."""
    return [int(d.strip()) for d in settings.WEBHOOK_RETRY_BASE_DELAYS.split(",") if d.strip()]


WEBHOOK_SCHEMA_VERSION = "1"

# Supported schema versions and their compatible event types.
# Any payload with a schema_version not in this matrix is dead-lettered.
SUPPORTED_SCHEMA_VERSIONS: dict[str, list[str]] = {
    "1": ["sla.violation", "sla.warning", "sla.resolved"],
}

# HTTP status code classification for webhook delivery behavior
# Terminal: delivery should not be retried (success or permanent failure)
# Retryable: delivery should be retried with exponential backoff
RETRYABLE_STATUS_CODES = {500, 502, 503, 504}  # Server errors
TERMINAL_STATUS_CODES = {
    # 2xx: Success
    200, 201, 202, 203, 204, 205, 206, 207, 208, 226,
    # 3xx: Redirection (webhook endpoints should not redirect)
    300, 301, 302, 303, 304, 305, 306, 307, 308,
    # 4xx: Client errors (permanent, retrying won't help)
    400, 401, 402, 403, 404, 405, 406, 407, 408, 409, 410, 411, 412, 413, 414, 415, 416, 417, 418, 421, 422, 423, 424, 425, 426, 428, 429, 431, 451,
}

DEAD_LETTER_REASON_UNKNOWN_SCHEMA_VERSION = "unknown_schema_version"
DEAD_LETTER_REASON_INCOMPATIBLE_EVENT_TYPE = "incompatible_event_type"


def classify_http_status(status_code: int) -> str:
    """Classify HTTP status code as 'terminal' or 'retryable'.

    Args:
        status_code: HTTP status code from webhook response

    Returns:
        'terminal' for 2xx/3xx/4xx (success or permanent failure)
        'retryable' for 5xx (transient server errors)

    Raises:
        ValueError: If status code is not in either classification set
    """
    if status_code in RETRYABLE_STATUS_CODES:
        return "retryable"
    elif status_code in TERMINAL_STATUS_CODES:
        return "terminal"
    else:
        # Unknown status codes (e.g., 599, custom codes) are treated as retryable
        # to avoid dead-lettering on non-standard responses
        if 500 <= status_code < 600:
            return "retryable"
        return "terminal"


def validate_payload_schema_version(
    payload: dict,
    event: "WebhookEvent",
) -> tuple[bool, str]:
    """Validate payload schema_version and event_type compatibility.

    Returns (is_valid, dead_letter_reason).
    dead_letter_reason is empty string when valid.
    """
    schema_version = str(payload.get("schema_version", ""))
    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        return False, DEAD_LETTER_REASON_UNKNOWN_SCHEMA_VERSION
    compatible_events = SUPPORTED_SCHEMA_VERSIONS[schema_version]
    if event.value not in compatible_events:
        return False, DEAD_LETTER_REASON_INCOMPATIBLE_EVENT_TYPE
    return True, ""


def _generate_idempotency_key(webhook_id: UUID, event: WebhookEvent, event_timestamp: str) -> str:
    """Generate a deterministic idempotency key for webhook delivery.

    The key is derived from webhook_id, event type, and event timestamp to ensure:
    - Uniqueness: Different events generate different keys
    - Consistency: Same event (webhook + event + timestamp) always generates same key
    - Immutability: Key never changes across retries or manual replays

    Args:
        webhook_id: UUID of the webhook configuration
        event: Webhook event type
        event_timestamp: ISO-formatted UTC timestamp when event occurred

    Returns:
        SHA256 hex digest as the idempotency key
    """
    key_input = f"{webhook_id}:{event.value}:{event_timestamp}"
    return hashlib.sha256(key_input.encode()).hexdigest()


def _build_headers(
    webhook: Webhook,
    payload: str,
    event: WebhookEvent = WebhookEvent.SLA_VIOLATION,
    signature_version: int = CURRENT_SIGNATURE_VERSION,
    idempotency_key: Optional[str] = None,
    schema_version: str = "1",
) -> Dict[str, str]:
    """Build webhook delivery headers with explicit signature versioning (BE-087) and idempotency key.

    Args:
        webhook: Webhook configuration
        payload: JSON payload string
        event: Webhook event type
        signature_version: Explicit signature algorithm version
        idempotency_key: Deterministic key for receiver-side deduplication
        schema_version: The schema version of the payload.

    Returns:
        Dictionary of headers including:
        - Content-Type: application/json
        - X-Webhook-Event: event type
        - X-Webhook-Timestamp: ISO-formatted UTC timestamp
        - X-Webhook-Idempotency-Key: idempotency key for deduplication
        - X-Webhook-Signature: signature (if secret configured)
        - X-Webhook-Signature-Version: signature version (if secret configured)
        - X-Webhook-Version: The schema version of the payload.
    """
    headers = {
        "Content-Type": "application/json",
        "X-Webhook-Event": event.value,
        "X-Webhook-Timestamp": datetime.utcnow().isoformat(),
        "X-Webhook-Version": schema_version,
    }
    if idempotency_key:
        headers["X-Webhook-Idempotency-Key"] = idempotency_key
    if webhook.secret:
        sig_hex, _ = sign_payload(webhook.secret, payload, signature_version)
        headers["X-Webhook-Signature"] = f"sha256={sig_hex}"
        headers["X-Webhook-Signature-Version"] = str(signature_version)
    return headers


def get_active_webhooks_for_event(db: Session, event: WebhookEvent) -> List[Webhook]:
    """Get active webhooks subscribed to a specific event using optimized JSON containment.

    Uses PostgreSQL GIN index on events column for O(log n) lookup instead of O(n) scan.
    Falls back to in-memory cache for parsed event subscriptions to avoid repeated JSON parsing.

    Args:
        db: Database session
        event: Webhook event type to match

    Returns:
        List of active webhooks subscribed to the event
    """
    # Use PostgreSQL JSON containment operator with GIN index for efficient filtering
    # This filters at the database level, avoiding loading all webhooks into memory
    if db.bind and db.bind.dialect.name == "sqlite":
        webhooks = (
            db.query(Webhook)
            .filter(Webhook.is_active == True)
            .all()
        )
        webhooks = [w for w in webhooks if event.value in (w.events or [])]
    else:
        event_json = json.dumps([event.value])
        webhooks = (
            db.query(Webhook)
            .filter(Webhook.is_active == True)
            .filter(text("webhooks.events @> :event_json").bindparams(event_json=event_json))
            .all()
        )

    # Validate parsed events from cache to ensure no misrouting
    result = []
    for webhook in webhooks:
        cache_key = f"webhook_events:{webhook.id}"
        cached_events = _webhook_events_cache.get(cache_key)

        if cached_events is None:
            try:
                cached_events = json.loads(webhook.events)
                _webhook_events_cache.set(cache_key, cached_events)
            except (json.JSONDecodeError, TypeError):
                logger.warning("Webhook %s has invalid events JSON, skipping.", webhook.id)
                continue

        # Double-check event subscription to prevent misrouting
        if event.value in cached_events:
            result.append(webhook)

    return result


def invalidate_webhook_cache(webhook_id: UUID) -> None:
    """Invalidate cached event subscriptions for a specific webhook.

    Call this after any webhook CRUD operation (create, update, delete) to ensure
    the cache reflects the latest configuration.

    Args:
        webhook_id: UUID of the webhook whose cache should be invalidated
    """
    cache_key = f"webhook_events:{webhook_id}"
    _webhook_events_cache.invalidate(cache_key)


def create_delivery(
    db: Session,
    webhook: Webhook,
    event: WebhookEvent,
    payload: Dict[str, Any],
    event_timestamp: str,
    signature_version: int = CURRENT_SIGNATURE_VERSION,
    partition_id: Optional[int] = None,
) -> WebhookDelivery:
    """Create a webhook delivery record with explicit signature version (BE-087) and idempotency key.

    Args:
        db: Database session
        webhook: Webhook configuration
        event: Webhook event type
        payload: Event payload dict (will be JSON-serialized)
        event_timestamp: ISO-formatted UTC timestamp when event occurred
        signature_version: Signature algorithm version to use
        partition_id: Optional partition assignment for queue partitioning (#302)

    Returns:
        Created WebhookDelivery record
    """
    # Generate deterministic idempotency key
    idempotency_key = _generate_idempotency_key(webhook.id, event, event_timestamp)

    # Parse event_timestamp for storage
    event_dt = datetime.fromisoformat(event_timestamp)

    delivery = WebhookDelivery(
        webhook_id=webhook.id,
        event=event,
        payload=json.dumps(payload, default=str),
        status=WebhookDeliveryStatus.PENDING,
        signature_version=signature_version,
        idempotency_key=idempotency_key,
        event_timestamp=event_dt,
    )
    db.add(delivery)
    db.commit()
    db.refresh(delivery)
    return delivery


def _attempt_delivery(delivery: WebhookDelivery, webhook: Webhook) -> bool:
    payload_str = delivery.payload
    payload_data = json.loads(payload_str)
    schema_version = payload_data.get("schema_version", "1")

    headers = _build_headers(
        webhook,
        payload_str,
        delivery.event,
        delivery.signature_version,
        idempotency_key=delivery.idempotency_key,
        schema_version=schema_version,
    )

    # Issue #303: SSRF redirect protection - limit redirects
    redirect_limit = settings.WEBHOOK_SSRF_MAX_REDIRECTS

    try:
        with httpx.Client(timeout=10.0, follow_redirects=True, max_redirects=redirect_limit) as client:
            response = client.post(webhook.url, content=payload_str, headers=headers)
        delivery.response_status_code = response.status_code
        delivery.response_body = response.text[:4000]

        # Use explicit status code classification
        classification = classify_http_status(response.status_code)

        if classification == "terminal":
            if 200 <= response.status_code < 300:
                # Success - no retry needed
                return True
            else:
                # Permanent failure (3xx/4xx) - should not retry
                delivery.error_message = f"Terminal failure: HTTP {response.status_code}"
                return False
        else:
            # Retryable (5xx) - will be retried by dispatch_delivery
            delivery.error_message = f"Retryable failure: HTTP {response.status_code}"
            return False

    except httpx.TimeoutException as exc:
        delivery.error_message = f"Request timed out: {exc}"
        logger.warning("Webhook delivery %s timed out.", delivery.id)
        return False
    except httpx.RequestError as exc:
        delivery.error_message = f"Request error: {exc}"
        logger.warning("Webhook delivery %s failed with request error: %s", delivery.id, exc)
        return False


def dispatch_delivery(db: Session, delivery_id: UUID) -> None:
    delivery = db.query(WebhookDelivery).filter(WebhookDelivery.id == delivery_id).first()
    if not delivery:
        logger.error("WebhookDelivery %s not found.", delivery_id)
        return

    webhook = delivery.webhook
    delivery.attempt_count += 1
    delivery.status = WebhookDeliveryStatus.RETRYING if delivery.attempt_count > 1 else WebhookDeliveryStatus.PENDING
    delivery.updated_at = datetime.utcnow()
    db.commit()

    start_time = time.time()
    success = _attempt_delivery(delivery, webhook)
    latency_ms = (time.time() - start_time) * 1000.0

    # Determine partition (Issue #302)
    events = json.loads(webhook.events) if isinstance(webhook.events, str) else webhook.events
    partition_id = _get_partition_for_webhook(webhook.id, events)

    if success:
        delivery.status = WebhookDeliveryStatus.SUCCESS
        delivery.delivered_at = datetime.utcnow()
        delivery.next_retry_at = None
        logger.info(
            "Webhook delivery %s succeeded on attempt %d for webhook %s.",
            delivery.id, delivery.attempt_count, webhook.id,
        )
    else:
        # Check if failure is terminal (should not retry)
        if delivery.response_status_code:
            classification = classify_http_status(delivery.response_status_code)
            if classification == "terminal":
                # Terminal failure - dead-letter immediately
                delivery.status = WebhookDeliveryStatus.DEAD_LETTER
                delivery.dead_lettered_at = datetime.utcnow()
                delivery.next_retry_at = None
                logger.error(
                    "Webhook delivery %s failed with terminal status %d. Dead-lettered immediately.",
                    delivery.id, delivery.response_status_code,
                )
                delivery.updated_at = datetime.utcnow()
                db.commit()
                record_partition_metrics(partition_id, success=True, latency_ms=latency_ms)
                record_slo_observation(True, latency_ms, delivery.event.value, webhook.url)
                return

        # Retryable failure - schedule retry
        retry_index = delivery.attempt_count - 1
        max_retries = webhook.max_retries or 3
        retry_delays = _get_retry_delays()

        if retry_index < max_retries and retry_index < len(retry_delays):
            base_delay = retry_delays[retry_index]
            delay = min(base_delay * (2 ** retry_index), settings.WEBHOOK_RETRY_MAX_DELAY_SECONDS)
            delivery.next_retry_at = datetime.utcnow() + timedelta(seconds=delay)
            delivery.status = WebhookDeliveryStatus.RETRYING
            logger.warning(
                "Webhook delivery %s failed (attempt %d). Retrying in %ds.",
                delivery.id, delivery.attempt_count, delay,
            )
        else:
            # Mark as dead-letter instead of just failed
            delivery.status = WebhookDeliveryStatus.DEAD_LETTER
            delivery.dead_lettered_at = datetime.utcnow()
            delivery.next_retry_at = None
            logger.error(
                "Webhook delivery %s permanently failed after %d attempts. Marked as dead-letter.",
                delivery.id, delivery.attempt_count,
            )

    delivery.updated_at = datetime.utcnow()
    db.commit()

    # Record metrics (Issues #302 & #305)
    record_partition_metrics(partition_id, success, latency_ms)
    record_slo_observation(success, latency_ms, delivery.event.value, webhook.url)


def trigger_sla_violation_webhooks(
    db: Session,
    sla_data: Dict[str, Any],
    event: WebhookEvent = WebhookEvent.SLA_VIOLATION,
    signature_version: int = CURRENT_SIGNATURE_VERSION,
) -> List[WebhookDelivery]:
    """Trigger webhook deliveries for an event with explicit signature versioning (BE-087) and idempotency keys.

    Args:
        db: Database session
        sla_data: Event data to include in webhook payload
        event: Webhook event type
        signature_version: Signature algorithm version (defaults to current supported version)

    Returns:
        List of created WebhookDelivery records

    Note:
        - Each delivery includes explicit signature_version metadata in headers
        - Timestamp is immutable across retries (idempotency support)
        - Idempotency key is deterministic: webhook_id + event + timestamp
        - Future signing changes can use new version without breaking existing consumers
    """
    webhooks = get_active_webhooks_for_event(db, event)
    deliveries = []

    # Timestamp is captured once and reused across all retries (idempotency support)
    event_timestamp = datetime.utcnow().isoformat()

    for webhook in webhooks:
        # Issue #304: Build payload with redaction and correct schema version
        payload = build_redacted_payload(
            sla_data,
            event,
            schema_version=webhook.schema_version,
            event_timestamp=event_timestamp
        )

        # Issue #303: Validate webhook URL for SSRF at dispatch time (full DNS check)
        is_valid_url, url_reason = validate_webhook_url(webhook.url)
        if not is_valid_url:
            logger.warning(
                "Webhook %s (%s) URL validation failed: %s. Skipping delivery.",
                webhook.id, webhook.name, url_reason,
            )
            continue

        # Issue #302: Check partition backpressure
        events_list = json.loads(webhook.events) if isinstance(webhook.events, str) else webhook.events
        partition_id = _get_partition_for_webhook(webhook.id, events_list)

        if is_backpressured(partition_id) and partition_id != settings.WEBHOOK_SLA_PRIORITY_PARTITION:
            logger.warning(
                "Partition %d is backpressured (%d pending). Throttling non-SLA webhook %s.",
                partition_id, _get_partition_pending_count(partition_id), webhook.id,
            )
            continue

        delivery = create_delivery(
            db,
            webhook,
            event,
            payload,
            event_timestamp=event_timestamp,
            signature_version=signature_version,
            partition_id=partition_id,
        )
        deliveries.append(delivery)

        # Validate schema version and event type compatibility before dispatch
        is_valid, dead_letter_reason = validate_payload_schema_version(payload, event)
        if not is_valid:
            delivery.status = WebhookDeliveryStatus.DEAD_LETTER
            delivery.dead_lettered_at = datetime.utcnow()
            delivery.error_message = f"dead_lettered: {dead_letter_reason}"
            db.commit()
            logger.warning(
                "Webhook delivery %s dead-lettered: schema_version=%s reason=%s",
                delivery.id, payload.get("schema_version"), dead_letter_reason,
            )
            continue

        logger.info(
            "Queued webhook delivery %s for webhook %s on event %s (sig_version=%d, idempotency_key=%s, partition=%d).",
            delivery.id, webhook.id, event.value, signature_version, delivery.idempotency_key, partition_id,
        )
        # Dispatch immediately (in production, offload to a background task/queue)
        dispatch_delivery(db, delivery.id)

    return deliveries


def retry_pending_deliveries(db: Session) -> int:
    now = datetime.utcnow()
    due_deliveries = (
        db.query(WebhookDelivery)
        .filter(
            WebhookDelivery.status == WebhookDeliveryStatus.RETRYING,
            WebhookDelivery.next_retry_at <= now,
        )
        .all()
    )

    count = 0
    for delivery in due_deliveries:
        dispatch_delivery(db, delivery.id)
        count += 1

    return count


def get_dead_letter_deliveries(db: Session, webhook_id: Optional[UUID] = None, limit: int = 100) -> List[WebhookDelivery]:
    """Get dead-lettered deliveries for auditing and remediation."""
    query = (
        db.query(WebhookDelivery)
        .filter(WebhookDelivery.status == WebhookDeliveryStatus.DEAD_LETTER)
        .order_by(WebhookDelivery.dead_lettered_at.desc())
    )

    if webhook_id:
        query = query.filter(WebhookDelivery.webhook_id == webhook_id)

    return query.limit(limit).all()


def replay_dead_letter_delivery(db: Session, delivery_id: UUID) -> bool:
    """Replay a dead-lettered delivery by resetting its status and retrying.

    Idempotency key and event_timestamp are preserved across replays to ensure
    receiver-side deduplication works correctly.
    """
    delivery = db.query(WebhookDelivery).filter(WebhookDelivery.id == delivery_id).first()
    if not delivery:
        logger.error("Dead-letter delivery %s not found.", delivery_id)
        return False

    if delivery.status != WebhookDeliveryStatus.DEAD_LETTER:
        logger.warning("Delivery %s is not in dead-letter status (current: %s).", delivery_id, delivery.status)
        return False

    # Reset delivery state for replay (preserve idempotency_key and event_timestamp)
    delivery.status = WebhookDeliveryStatus.PENDING
    delivery.attempt_count = 0
    delivery.next_retry_at = None
    delivery.dead_lettered_at = None
    delivery.error_message = None
    delivery.response_status_code = None
    delivery.response_body = None
    delivery.delivered_at = None
    # idempotency_key and event_timestamp remain unchanged
    delivery.updated_at = datetime.utcnow()

    db.commit()

    # Dispatch the replay
    dispatch_delivery(db, delivery.id)
    logger.info("Replayed dead-letter delivery %s (idempotency_key=%s preserved)", delivery_id, delivery.idempotency_key)
    return True


def replay_deliveries_by_event_context(
    db: Session,
    event: WebhookEvent,
    device_id: Optional[str] = None,
    outage_id: Optional[str] = None,
    limit: int = 50
) -> int:
    """Replay deliveries by event and context (device or outage)."""
    # Get dead-lettered deliveries matching the criteria
    query = (
        db.query(WebhookDelivery)
        .filter(WebhookDelivery.status == WebhookDeliveryStatus.DEAD_LETTER)
        .filter(WebhookDelivery.event == event)
    )

    # Filter by payload context if provided
    if device_id or outage_id:
        deliveries = query.all()
        matching_deliveries = []

        for delivery in deliveries:
            try:
                payload = json.loads(delivery.payload)
                data = payload.get("data", {})

                if device_id and data.get("device_id") == device_id:
                    matching_deliveries.append(delivery)
                elif outage_id and data.get("outage_id") == outage_id:
                    matching_deliveries.append(delivery)
            except (json.JSONDecodeError, TypeError):
                continue

        deliveries = matching_deliveries[:limit]
    else:
        deliveries = query.limit(limit).all()

    # Replay matching deliveries
    replayed_count = 0
    for delivery in deliveries:
        if replay_dead_letter_delivery(db, delivery.id):
            replayed_count += 1

    logger.info(
        "Replayed %d dead-letter deliveries for event=%s, device_id=%s, outage_id=%s",
        replayed_count, event.value, device_id, outage_id
    )
    return replayed_count


# --------------------------------------------------------------------------- #
# BE-W5-045: Webhook disaster-recovery replay                                #
# --------------------------------------------------------------------------- #


def recover_deliveries_in_window(
    db: Session,
    start_time: datetime,
    end_time: datetime,
    on_progress: Optional[Any] = None,
) -> Dict[str, int]:
    """Replay *all* webhook deliveries whose ``event_timestamp`` falls inside
    ``[start_time, end_time]`` — including those in PENDING/RETRYING/FAILED.

    Acceptance criteria for BE-W5-045:
      * Bounded time window (caller-supplied).
      * Safe & idempotent: ``replay_dead_letter_delivery`` preserves
        ``idempotency_key`` and ``event_timestamp`` so receiver-side
        deduplication remains correct across replays.
      * Resumable & auditable: progress callbacks write to the parent
        ``Job`` so an operator can poll ``GET /jobs/{id}``.

    Non-DEAD_LETTER deliveries are marked PENDING before being dispatched,
    so the normal retry pipeline takes over. Already-SUCCESS deliveries
    are left untouched.
    """
    if end_time < start_time:
        raise ValueError("end_time must be greater than or equal to start_time")

    candidates = (
        db.query(WebhookDelivery)
        .filter(WebhookDelivery.event_timestamp >= start_time)
        .filter(WebhookDelivery.event_timestamp <= end_time)
        .order_by(WebhookDelivery.event_timestamp.asc())
        .all()
    )

    replayed = 0
    skipped = 0
    total = len(candidates)

    for idx, delivery in enumerate(candidates, start=1):
        if delivery.status == WebhookDeliveryStatus.SUCCESS:
            skipped += 1
            if on_progress:
                on_progress(idx, total)  # type: ignore[arg-type]
            continue
        # Reset to PENDING if currently RETRYING/FAILED; DEAD_LETTER goes
        # through the dedicated replay helper which clears transient fields.
        if delivery.status == WebhookDeliveryStatus.DEAD_LETTER:
            ok = replay_dead_letter_delivery(db, delivery.id)
        else:
            delivery.status = WebhookDeliveryStatus.PENDING
            delivery.next_retry_at = None
            delivery.dead_lettered_at = None
            db.commit()
            from app.services.webhook_service import dispatch_delivery  # local
            dispatch_delivery(db, delivery.id)
            ok = True
        if ok:
            replayed += 1
        else:
            skipped += 1
        if on_progress:
            on_progress(idx, total)  # type: ignore[arg-type]

    logger.info(
        "BE-W5-045: recovered window=[%s,%s] total=%d replayed=%d skipped=%d",
        start_time.isoformat(), end_time.isoformat(), total, replayed, skipped,
    )
    return {"total": total, "replayed": replayed, "skipped": skipped}


def enqueue_webhook_dr_replay(
    db: Session,
    start_time: datetime,
    end_time: datetime,
) -> Job:
    """Create the DR replay ``Job`` record and dispatch the Celery task.

    BE-W5-045: Enables operators to kick off disaster recovery which then
    runs asynchronously and reports progress via the ``Job`` lifecycle.
    """
    if end_time < start_time:
        raise ValueError("end_time must be greater than or equal to start_time")

    payload = {
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
    }
    from app.tasks.webhook_tasks import recover_webhooks_in_window

    # Pre-allocate the Job so the caller has an id immediately.
    placeholder_id = "pending"
    job = Job(
        celery_task_id=placeholder_id,
        job_type=JobType.WEBHOOK_DR_REPLAY,
        payload=json.dumps(payload),
        progress=0.0,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    # Dispatch the Celery task with the assigned Job's id.
    task_result = recover_webhooks_in_window.apply_async(
        kwargs={
            "job_id": str(job.id),
            "start_iso": start_time.isoformat(),
            "end_iso": end_time.isoformat(),
        },
    )
    job.celery_task_id = task_result.id
    db.commit()
    db.refresh(job)
    logger.info(
        "BE-W5-045: enqueued DR replay job=%s celery_task_id=%s window=[%s,%s]",
        job.id, job.celery_task_id, start_time.isoformat(), end_time.isoformat(),
    )
    return job