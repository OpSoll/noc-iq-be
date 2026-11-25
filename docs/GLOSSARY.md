# Glossary

This glossary defines the core domain terms used across NOC IQ. Use it to stay
aligned on meanings when reading the API, docs, and source code.

## Core terms

### SLA breach

When a monitored service fails to meet a contracted metric, such as an uptime
percentage or response-time target, during a defined measurement window.

- **Affected module**: `app/services/sla/sla_calculator.py`
- **Related**: `app/api/v1/endpoints/sla.py`

### MTTR (Mean Time to Repair)

The average time it takes to restore a service after an outage. Short MTTR
usually indicates strong incident response and reliable recovery tooling.

- **Affected module**: `app/services/sla`
- **Related**: outage lifecycle endpoints in `app/api/v1/endpoints/outages.py`

### SLA (Service Level Agreement)

A contractual promise that a service will meet defined reliability and
performance targets for a measurement period.

- **Affected module**: `app/services/sla`
- **Related**: `app/models/sla_record.py`

### Reward

The amount a contributor or operations team receives after a service meets the
SLA for the period. Rewards are distributed as points or tokens.

- **Affected module**: `app/services/payments`
- **Related**: `app/api/v1/endpoints/payments.py`

### Penalty

A deduction from a reward or account balance when an SLA is breached.

- **Affected module**: `app/services/payments`
- **Related**: `app/api/v1/endpoints/outages.py`

### Outage

A documented period during which a service was unavailable or degraded.

- **Affected module**: `app/models/outage.py`
- **Related**: `app/api/v1/endpoints/outages.py`

### Job

A background task that runs idempotently and can be retried, quarantined, or
cleaned up according to retention policy.

- **Affected module**: `app/models/job.py`
- **Related**: `app/services/job_cleanup.py`

### Dead letter

A job that has exhausted its retry budget and must be routed for manual or
automated handling instead of being retried automatically.

- **Affected module**: `app/services/job_cleanup.py`
- **Related**: `app/api/v1/endpoints/jobs.py`

### Job retention

The time window during which completed job records are retained before cleanup
sweep deletes them.

- **Affected module**: `app/services/job_cleanup.py`
- **Related**: retention-stats and cleanup endpoints

### Retention tier

A configured policy that groups job types or statuses into separate retention
windows so that different classes of records can be kept or cleaned at different
rates.

- **Affected module**: `app/services/job_cleanup.py`
- **Related**: tests in `tests/test_mercy60_issues.py`

### Protected job

A job that must be retained regardless of the standard cleanup policy, for
example a quarantined or audit-critical job.

- **Affected module**: `app/services/job_cleanup.py`
- **Related**: `tests/test_mercy60_issues.py`

### Audit-critical job

A job whose outcome is used for compliance, audit, or dispute purposes and must
not be deleted during standard cleanup sweeps.

- **Affected module**: `app/services/job_cleanup.py`
- **Related**: `app/api/v1/endpoints/jobs.py`

### Contract RPC

The HTTP/WebSocket endpoint exposed by the blockchain node that NOC IQ uses to
read contract state and submit transaction data.

- **Affected module**: `app/core/config.py`
- **Related**: `app/services/contract_adapter.py`

### Background job

A long-running task executed asynchronously, typically by Celery.

- **Affected module**: `app/tasks`
- **Related**: `app/services/job_cleanup.py`

### Worker lease

A time-bound lock that lets a worker claim ownership of a job. Leases are used
to reclaim stuck jobs when a worker crashes or disconnects.

- **Affected module**: `app/services/job_cleanup.py`
- **Related**: heartbeat and stale-lease reclamation endpoints

### Quarantined job

A job that was determined to be corrupt or unsafe and has been isolated so it
cannot affect normal processing.

- **Affected module**: `app/models/job.py`
- **Related**: `app/api/v1/endpoints/jobs.py`

### Webhook delivery

A retryable event sent to an external endpoint, where failures can escalate to
quarantine or dead-letter per the configured policy.

- **Affected module**: `app/services/webhook_tasks.py`
- **Related**: `app/api/v1/endpoints/webhooks.py`

### Cancellation token

A lightweight object used to stop or redirect a background job so it stops
working on stale or superseded work.

- **Affected module**: `app/services`
- **Related**: `app/tasks`

## Cross-references

- API contract for jobs: `docs/API.md`
- Retention and cleanup implementation: `app/services/job_cleanup.py`
- Job model fields: `app/models/job.py`
- Operational failures runbook: `docs/OPERATIONAL_FAILURES_RUNBOOK.md`
