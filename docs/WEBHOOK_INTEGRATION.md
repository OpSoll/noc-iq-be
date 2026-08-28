# Webhook Integration Guide

## Overview

Webhooks enable real-time event delivery with built-in security and versioning support. This guide covers signature verification, timestamp validation, and forward-compatible event handling.

## Authentication & Signature Verification

### Signature Headers

Each webhook delivery includes explicit signature versioning metadata:

```
X-Webhook-Signature: sha256={hex_digest}
X-Webhook-Signature-Version: 1
X-Webhook-Timestamp: 2026-04-29T14:30:45.123456
```

### Current Signature Version (v1): HMAC-SHA256

**Algorithm:**
- Compute: `HMAC-SHA256(secret_key, payload_json)`
- Output: Hex-encoded digest string
- Envelope: `sha256={hex_digest_value}`

**Verification Example (Python):**

```python
import hmac
import hashlib
import json

def verify_webhook(request_body: str, signature_header: str, secret: str) -> bool:
    """Verify webhook signature using HMAC-SHA256."""
    # Extract hex digest (remove 'sha256=' prefix)
    provided_signature = signature_header.replace('sha256=', '')

    # Compute expected signature
    expected_signature = hmac.new(
        secret.encode(),
        request_body.encode(),
        hashlib.sha256
    ).hexdigest()

    # Compare using constant-time comparison
    return hmac.compare_digest(expected_signature, provided_signature)
```

**Verification Example (Node.js):**

```javascript
const crypto = require('crypto');

function verifyWebhook(requestBody, signatureHeader, secret) {
  // Extract hex digest
  const providedSignature = signatureHeader.replace('sha256=', '');

  // Compute expected signature
  const expectedSignature = crypto
    .createHmac('sha256', secret)
    .update(requestBody)
    .digest('hex');

  // Compare using constant-time comparison
  return crypto.timingSafeEqual(
    Buffer.from(expectedSignature),
    Buffer.from(providedSignature)
  );
}
```

## Idempotency Keys

### Idempotency Key Specification

Each webhook delivery includes a deterministic idempotency key for receiver-safe deduplication:

- **Location**: `X-Webhook-Idempotency-Key` header
- **Format**: SHA256 hex digest (64 characters)
- **Determinism**: Derived from `webhook_id + event_type + event_timestamp`
- **Immutability**: Never changes across retries or manual replays
- **Uniqueness**: Guaranteed unique per webhook + event + timestamp combination

### Idempotency Key Algorithm

```python
import hashlib

def generate_idempotency_key(webhook_id: str, event: str, event_timestamp: str) -> str:
    """Generate deterministic idempotency key."""
    key_input = f"{webhook_id}:{event}:{event_timestamp}"
    return hashlib.sha256(key_input.encode()).hexdigest()
```

### Receiver-Side Deduplication

Use the idempotency key to prevent duplicate processing:

```python
from sqlalchemy import Column, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID

class ProcessedWebhook(Base):
    __tablename__ = "processed_webhooks"

    id = Column(UUID(as_uuid=True), primary_key=True)
    idempotency_key = Column(String(64), nullable=False, unique=True, index=True)
    processed_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint('idempotency_key', name='uq_processed_webhooks_idempotency'),
    )

def process_webhook(idempotency_key: str, payload: dict):
    # Check if already processed
    existing = db.query(ProcessedWebhook).filter(
        ProcessedWebhook.idempotency_key == idempotency_key
    ).first()

    if existing:
        logger.info(f"Webhook {idempotency_key} already processed, skipping")
        return

    # Process the webhook
    # ... your processing logic ...

    # Record as processed
    record = ProcessedWebhook(idempotency_key=idempotency_key)
    db.add(record)
    db.commit()
```

### Idempotency in Retry and Replay Scenarios

- **Automatic Retries**: Same idempotency key is preserved across all retry attempts
- **Manual Replay**: Dead-lettered deliveries retain their original idempotency key when replayed
- **Event Timestamp**: The `event_timestamp` field in the delivery record is immutable and used in key generation

### Delivery API Metadata

The delivery API exposes idempotency metadata for consumers:

```json
{
  "id": "uuid",
  "webhook_id": "uuid",
  "event": "sla.violation",
  "status": "success",
  "idempotency_key": "a1b2c3d4e5f6...",
  "event_timestamp": "2026-04-29T14:30:45.123456",
  "created_at": "2026-04-29T14:30:46.000000"
}
```

## Timestamp Validation Semantics

### Timestamp Specification

- **Location**: Top-level `timestamp` field in JSON payload
- **Format**: ISO 8601 with microseconds: `2026-04-29T14:30:45.123456`
- **Timezone**: UTC (Zulu time implied)
- **Immutability**: Identical across all retry attempts and signature versions
- **Duration**: Captured when event is triggered, not when delivery is attempted

### Timestamp Usage Patterns

#### 1. Idempotency Support

Detect and deduplicate retried deliveries using webhook_id + timestamp combination:

```python
# Database unique constraint or application logic
@unique
class DeliveredWebhookRecord:
    webhook_id: UUID
    timestamp: datetime
    event_type: str
    payload_hash: str
```

**Benefit**: If a webhook is retried due to network failure or crash recovery, receivers can identify and skip duplicates using timestamp + webhook_id.

#### 2. Freshness Validation (Optional)

Reject webhooks outside a configurable time window:

```python
from datetime import datetime, timedelta

def validate_webhook_freshness(
    webhook_timestamp: str,
    max_age_seconds: int = 3600,  # 1 hour grace period
) -> bool:
    """Check if webhook is within acceptable age window."""
    event_time = datetime.fromisoformat(webhook_timestamp)
    current_time = datetime.utcnow()
    age = (current_time - event_time).total_seconds()

    if age > max_age_seconds:
        # Log and reject as suspicious/stale
        return False
    if age < 0:
        # Clock skew or future event
        return False

    return True
```

**Recommended Windows**:
- Minimum: -5 seconds (account for receiver clock skew)
- Maximum: 1-24 hours (depends on application requirements)

#### 3. Audit Trails & Reconciliation

Correlate event occurrence time with delivery/processing time:

```python
def audit_webhook_delivery(delivery_record):
    """Create audit trail with timing information."""
    event_occurred = parse_iso8601(delivery_record['timestamp'])
    delivery_attempted = delivery_record['created_at']
    delivery_succeeded = delivery_record['delivered_at']

    latency_seconds = (delivery_attempted - event_occurred).total_seconds()
    processing_time = (delivery_succeeded - delivery_attempted).total_seconds()

    audit_log({
        'event_type': delivery_record['event'],
        'event_occurred': event_occurred,
        'latency': latency_seconds,
        'processing_time': processing_time,
        'delivered': delivery_succeeded is not None,
    })
```

## Signature Version Evolution

The explicit `X-Webhook-Signature-Version` header enables safe algorithm evolution:

### Forward Compatibility Strategy

1. **New Algorithm Rollout** (e.g., EdDSA to replace SHA256):
   - Deploy new signer generating both old (v1) and new (v2) signatures in parallel
   - Receivers see: `X-Webhook-Signature-Version: 2` + new header with v2 signature
   - Old receivers ignore headers they don't recognize (backward compatible)
   - New receivers validate v2 signatures

2. **Graceful Deprecation**:
   ```
   Phase 1: Deploy dual-signing (v1 + v2)
   Phase 2: Monitor adoption of v2 signature validation
   Phase 3: After 6+ months, deprecate v1 signing
   Phase 4: Remove v1 code after consumers migrate
   ```

3. **Algorithm Migration Example**:
   ```python
   # Future opportunity: add v2 when needed
   def sign_payload(secret: str, payload: str, version: int = 1) -> Tuple[str, int]:
       if version == 1:
           return sign_payload_v1(secret, payload), 1
       elif version == 2:  # Future EdDSA implementation
           return sign_payload_v2(secret, payload), 2
       else:
           raise ValueError(f"Unsupported signature version: {version}")
   ```

## Webhook Delivery Contract

### Request Format

All webhook requests use:
- **Method**: POST
- **Content-Type**: application/json
- **Timeout**: 10 seconds
- **Retries**: 3 exponential backoff attempts (30s, 120s, 600s base with jitter)

### Payload Structure

```json
{
  "schema_version": "1",
  "event": "sla.violation",
  "timestamp": "2026-04-29T14:30:45.123456",
  "data": {
    "device_id": "dev-123",
    "outage_id": "out-456",
    "severity": "high",
    "sla_violated": true
  }
}
```

### Response Contract

- **Success**: HTTP 2xx response (body ignored)
- **Transient Failure**: HTTP 5xx → retry scheduled with exponential backoff
- **Permanent Failure**: HTTP 3xx/4xx → dead-lettered immediately (no retry)
- **Timeout**: No response in 10s → retry scheduled
- **Max Retries**: 3 total attempts for retryable failures, then dead-letter

### HTTP Status Code Classification

Webhook delivery behavior is explicitly codified by HTTP status code class:

| Status Class | Codes | Behavior | Rationale |
|-------------|-------|----------|-----------|
| **2xx Success** | 200, 201, 202, 203, 204, 205, 206, 207, 208, 226 | Terminal (success) | Delivery succeeded, no retry needed |
| **3xx Redirection** | 300, 301, 302, 303, 304, 305, 306, 307, 308 | Terminal (failure) | Webhook endpoints should not redirect; indicates misconfiguration |
| **4xx Client Error** | 400, 401, 402, 403, 404, 405, 406, 407, 408, 409, 410, 411, 412, 413, 414, 415, 416, 417, 418, 421, 422, 423, 424, 425, 426, 428, 429, 431, 451 | Terminal (failure) | Permanent client-side errors; retrying won't help |
| **5xx Server Error** | 500, 502, 503, 504 | Retryable | Transient server-side errors; may resolve with retry |

**Policy Metadata Endpoint**: Retrieve the current status code classification policy via `GET /webhooks/metadata`.

**Dead-Letter Behavior**:
- Terminal failures (3xx/4xx) are dead-lettered immediately on first attempt
- Retryable failures (5xx) are retried up to `max_retries` times before dead-lettering
- Unknown status codes outside the explicit sets are classified conservatively:
  - 5xx range → retryable
  - Other → terminal

## Security Best Practices

1. **Secret Management**:
   - Generate cryptographically random secrets (≥32 bytes)
   - Store on receiver side in secure configuration
   - Rotate periodically (webhook `secret_version` tracks rotations)

2. **Signature Verification**:
   - Always verify signature before processing payload
   - Use constant-time comparison (timing-attack resistant)
   - Log verification failures for security audits

3. **Timestamp Validation**:
   - Validate timestamp freshness (optional but recommended)
   - Use for idempotency deduplication (required for safe retries)
   - Log discrepancies > 1 hour for investigation

4. **Network Security**:
   - Use HTTPS endpoints (enforce in production)
   - Consider mutual TLS for high-security environments
   - Monitor webhook delivery success rates and latencies

## Operational Considerations

### Dead-Letter Queue & Replay

Deliveries that fail after all retries are marked as `dead_letter` status:

```python
# Query dead-lettered webhooks for auditing
GET /webhooks/{webhook_id}/deliveries?status=dead_letter

# Manually replay a failed delivery after investigation/fix
POST /webhooks/{webhook_id}/deliveries/{delivery_id}/replay
```

### Monitoring

Track these metrics:
- Delivery success rate (by event type)
- Retry rate and backoff effectiveness
- Average delivery latency
- Dead-letter rate (indicator of persistent problems)

### Timestamp Handling in Receivers

- Parse using ISO 8601 library (don't regex parse)
- Handle microseconds/fractional seconds correctly
- Consider receiver timezone (should normalize to UTC internally)
- Store as UTC datetime (not string) for comparison

## References

- [RFC 5869: HMAC-based Key Derivation Function](https://tools.ietf.org/html/rfc5869)
- [ISO 8601 DateTime Format](https://en.wikipedia.org/wiki/ISO_8601)
- [OWASP: Timing Attack Prevention](https://owasp.org/www-community/attacks/Timing_attack)

## Payload Schema Version Matrix (BE-W5-033)

Each outbound webhook payload includes a `schema_version` field. Unknown versions are dead-lettered with an explicit reason before delivery is attempted.

### Supported Versions

| schema_version | Compatible event_types                          | Status  |
|----------------|-------------------------------------------------|---------|
| `"1"`          | `sla.violation`, `sla.warning`, `sla.resolved`  | Active  |

### Dead-Letter Reasons

| Reason                        | Description                                                      |
|-------------------------------|------------------------------------------------------------------|
| `unknown_schema_version`      | `schema_version` field is missing or not in the supported list   |
| `incompatible_event_type`     | Event type is not allowed for the given `schema_version`         |

### Consumer Upgrade Path

When a new schema version is introduced:

1. New payloads include `"schema_version": "2"` alongside the old version during transition.
2. Consumers inspect `schema_version` and apply the matching parsing logic.
3. Unknown versions should be logged and discarded gracefully on the consumer side.

```python
def process_webhook(payload: dict) -> None:
    schema_version = payload.get("schema_version")
    if schema_version == "1":
        handle_v1(payload)
    elif schema_version == "2":
        handle_v2(payload)
    else:
        logger.warning("Unknown schema_version: %s — ignoring payload", schema_version)
```

---

## BE-W5-041: Webhook Batch Dispatch Backpressure and Queue Partitioning (Issue #302)

### Overview

Webhook dispatch workloads are partitioned to prevent slow tenants from blocking global delivery. SLA-critical events are routed to dedicated priority partitions to prevent starvation.

### Partition Strategy

| Partition ID | Purpose | Backpressure Threshold | Max Pending |
|-------------|---------|------------------------|-------------|
| 0 (Priority) | SLA events (violation, warning, resolved) | None (bypasses backpressure) | N/A |
| 1-N | General / tenant-specific traffic | Configurable via `WEBHOOK_PARTITION_BACKPRESSURE_THRESHOLD` | `WEBHOOK_PARTITION_MAX_PENDING` |

### Backpressure Behavior

When a non-priority partition exceeds the backpressure threshold:
- Non-SLA deliveries to that partition are deferred
- The priority partition continues uninterrupted
- Operational metrics expose partition lag and throughput

### Configuration

```
WEBHOOK_PARTITION_COUNT=4
WEBHOOK_PARTITION_BACKPRESSURE_THRESHOLD=500
WEBHOOK_PARTITION_MAX_PENDING=2000
WEBHOOK_ENDPOINT_PARTITION_ENABLED=True
WEBHOOK_SLA_PRIORITY_PARTITION=0
WEBHOOK_PAYMENT_PRIORITY_PARTITION=1
```

### Operational Metrics

Monitor partition health via `GET /webhooks/partitions`:

```json
{
  "partition_count": 4,
  "priority_partition": 0,
  "partitions": {
    "0": { "pending": 12, "throughput": 145.2 },
    "1": { "pending": 3, "throughput": 89.1 },
    "2": { "pending": 450, "throughput": 23.4 },
    "3": { "pending": 8, "throughput": 102.7 }
  },
  "global_pending": 473,
  "global_throughput": 360.4
}
```

### Autoscaling

The autoscaler (`WebhookAutoscaler`) monitors per-partition queue depth and adjusts worker counts:

- **Scale Up**: Triggered when any partition exceeds `WEBHOOK_QUEUE_SCALE_UP_THRESHOLD`
- **Scale Down**: Triggered when total queue depth falls below `WEBHOOK_QUEUE_SCALE_DOWN_THRESHOLD`
- **Priority Boost**: Priority partitions always get at least one dedicated worker

---

## BE-W5-042: Webhook Endpoint Validation Hardening and SSRF Safeguards (Issue #303)

### URL Validation Policy

Webhook URLs are validated against SSRF (Server-Side Request Forgery) attacks on creation, update, and at dispatch time.

### Blocked Targets

| Category | Examples |
|----------|----------|
| **Private Networks** | 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 127.0.0.0/8 |
| **Link-Local** | 169.254.0.0/16, fe80::/10 |
| **Loopback** | 127.0.0.1, ::1 |
| **Known Metadata Endpoints** | 169.254.169.254, metadata.google.internal, metadata.aws.internal |
| **Blocked Hostnames** | localhost, localhost.localdomain, localhost6, localhost6.localdomain6 |

### DNS Rebinding Protection

- Hostnames are resolved at validation time
- All resolved IPs are checked against the blocked ranges
- If ANY resolved IP is blocked, the URL is rejected

### Redirect Protection

- Redirects are limited to `WEBHOOK_SSRF_MAX_REDIRECTS` (default: 3)
- Prevents redirect chains that point to internal services

### Configuration

```
WEBHOOK_SSRF_BLOCKED_CIDRS=127.0.0.0/8,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,169.254.0.0/16,::1/128,fd00::/8,fe80::/10
WEBHOOK_SSRF_BLOCKED_HOSTNAMES=localhost,localhost.localdomain,localhost6,localhost6.localdomain6,metadata.google.internal,metadata.aws.internal,169.254.169.254
WEBHOOK_SSRF_ALLOW_PRIVATE=False
WEBHOOK_SSRF_ALLOW_LOOPBACK=False
WEBHOOK_SSRF_ALLOW_LINK_LOCAL=False
WEBHOOK_SSRF_MAX_REDIRECTS=3
```

---

## BE-W5-043: Webhook Payload Redaction Policy (Issue #304)

### Overview

Sensitive fields in webhook payloads are automatically redacted before delivery to prevent data leakage.

### Redacted Fields

The following field names are masked (case-insensitive matching):

```
seed, secret_seed, private_key, mnemonic, password,
token, access_token, refresh_token, signing_key, wallet_secret
```

### Redaction Behavior

- **Recursive**: Redaction applies to all nested dictionaries within the `data` payload
- **Deep**: Up to 10 levels of nesting are scanned
- **Masked Value**: `[REDACTED]` (configurable via `WEBHOOK_REDACTION_MASK`)
- **Disabled**: Redaction can be disabled globally via `WEBHOOK_REDACTION_ENABLED`

### Example

**Before Redaction:**
```json
{
  "schema_version": "1",
  "event": "sla.violation",
  "data": {
    "device_id": "dev-123",
    "wallet_secret": "SGFEPI...",
    "credentials": {
      "password": "super-secret-123",
      "token": "eyJhbGci..."
    }
  }
}
```

**After Redaction:**
```json
{
  "schema_version": "1",
  "event": "sla.violation",
  "data": {
    "device_id": "dev-123",
    "wallet_secret": "[REDACTED]",
    "credentials": {
      "password": "[REDACTED]",
      "token": "[REDACTED]"
    }
  }
}
```

### Audit Events

Redaction configuration changes are audited via the audit log:

| Event Type | Description |
|-----------|-------------|
| `redaction.config.changed` | Redaction field list was modified |
| `redaction.config.validated` | Redaction config passed validation |
| `redaction.config.failed` | Redaction config failed validation |
| `redaction.field_leak_detected` | Sensitive field detected in outbound payload |

---

## BE-W5-044: Webhook Delivery SLO Metrics and Alert Thresholds (Issue #305)

### SLO Definition

| Metric | Target | Description |
|--------|--------|-------------|
| **Success Rate** | 99.9% percentage of deliveries returning HTTP 2xx |
| **Latency (P95)** | ≤ 5000 ms | 95th percentile delivery latency |
| **Error Budget** | 0.1% | Maximum acceptable error rate |
| **Window** | 3600 s | Sliding measurement window (1 hour) |

### Burn Rate Alerts

| Alert | Threshold | Action |
|-------|-----------|--------|
| **Burn Rate Alert** | ≥ 2.0x | Error budget being consumed twice as fast as budgeted |
| **Budget Burn Alert** | ≤ 50% remaining | Critical: more than half the error budget consumed |
| **Latency Breach** | P95 > 5000ms | Latency SLO is being violated |

### Metrics Endpoint

Retrieve current SLO metrics:

```
GET /webhooks/slo-metrics
GET /metrics/webhook-slo
```

**Response**:
```json
{
  "status": "ok",
  "window_seconds": 3600,
  "slo_target": 0.999,
  "overall": {
    "total_deliveries": 1500,
    "successes": 1498,
    "success_rate": 0.998667,
    "avg_latency_ms": 245.3,
    "p95_latency_ms": 890.1,
    "p99_latency_ms": 2100.5
  },
  "burn_indicators": {
    "error_budget": 0.001,
    "actual_error_rate": 0.001333,
    "burn_rate": 1.333,
    "budget_remaining_pct": 33.33,
    "burn_rate_alert": false,
    "budget_burn_alert": true,
    "latency_breach": false
  },
  "per_event": {
    "sla.violation": {
      "total": 1200,
      "success_rate": 0.9992,
      "avg_latency_ms": 210.5,
      "p95_latency_ms": 750.0
    },
    "sla.warning": {
      "total": 300,
      "success_rate": 0.9967,
      "avg_latency_ms": 380.2,
      "p95_latency_ms": 1200.0
    }
  },
  "per_endpoint": {
    "https://consumer.example.com/webhook": {
      "total": 800,
      "success_rate": 0.9988
    }
  }
}
```

### Configuration

```
WEBHOOK_SLO_SUCCESS_TARGET=0.999
WEBHOOK_SLO_LATENCY_TARGET_MS=5000
WEBHOOK_SLO_BURN_RATE_THRESHOLD=2.0
WEBHOOK_SLO_WINDOW_SECONDS=3600
WEBHOOK_SLO_BUDGET_BURN_ALERT_PERCENT=50.0
```

### Response Playbook Hooks

When SLO alerts fire, the following actions are recommended:

| Alert | Immediate Action | Long-term Fix |
|-------|-----------------|---------------|
| **Burn Rate Alert** | Check webhook endpoint health, investigate 5xx errors | Add retry jitter, adjust backoff |
| **Budget Burn Alert** | Pause non-critical webhook dispatch | Scale workers, review infrastructure |
| **Latency Breach** | Check network latency, DNS resolution | Optimize delivery pipeline, add CDN |

### Cardinality Control

SLO metrics include per-event and per-endpoint dimensions with implicit cardinality controls:
- Per-event: Fixed set of known event types (sla.violation, sla.warning, sla.resolved)
- Per-endpoint: Distinct endpoints are tracked but the sliding window bounds total memory usage
- The `METRICS_CARDINALITY_BUDGET` setting limits total metric label combinations
