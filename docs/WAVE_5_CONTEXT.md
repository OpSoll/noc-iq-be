# Wave-Level Reliability Governance Framework

This document tracks backend engineering metrics boundaries required for production deployment authorizations.

## Scoring Index Criteria Matrix
The system health calculation uses a multi-faceted index formula to balance code stability and active operational metrics:

$$\text{Reliability Index} = (\text{SLO} \times 0.40) + (\text{Tests} \times 0.30) + (\text{Security} \times 0.20) + (\text{Incidents} \times 0.10)$$

## Release Block Conditions
A release candidate will receive an automatic **NO-GO** status if:
1. The combined `Reliability Index` drops lower than **85.0**.
2. Any unmitigated, open security vulnerability regressions exist inside core dependencies or containers.

## Worker Concurrency & Saturation Profiles (BE-W5-055)

Per-environment defaults selected by `APP_ENV` (case-insensitive;
unknown values fall back to `dev`). Any individual field may be overridden
by the corresponding `CELERY_WORKER_CONCURRENCY`, `CELERY_MAX_TASKS_PER_CHILD`,
or `DB_POOL_SIZE` setting without touching the table below.

| Environment | Worker Concurrency | Max Tasks / Child | DB Pool Size | Broker Max Connections | Threshold (broker) |
|-------------|--------------------|-------------------|--------------|------------------------|--------------------|
| `dev`       | 2                  | 100               | 5            | 100                    | 0.80               |
| `staging`   | 4                  | 500               | 10           | 100                    | 0.80               |
| `prod`      | 8                  | 1000              | 20           | 100                    | 0.80               |

### Saturation Guardrails

Two saturation channels are evaluated every 60 s by the
`app.tasks.celery_app.guardrail_check_task` beat job (see
`app/services/concurrency_guardrails.py`).

* **DB pool** — alert fires when `pool_saturation >= DB_GUARDRAIL_THRESHOLD`
  (default `0.75`). This threshold is **strictly below** the saturation
  threshold that `PoolSaturationMiddleware` uses to start rejecting (530/503)
  — i.e. `DB_POOL_SATURATION_THRESHOLD` (default `0.9`). The guardrail takes
  the lower of the two with a 0.05 headroom, so an operator tightening the
  rejection threshold below `0.8` will not silently neuter the alert.
* **Broker (Redis)** — alerts when
  `worker_count * concurrency >= BROKER_SATURATION_THRESHOLD *
  BROKER_MAX_CONNECTIONS`. The worker-count proxy comes from
  `celery_app.control.inspect().active_queues()`.

When an alert fires:
1. A `WARNING` log line is emitted with `[GUARDRAIL ALERT]` prefix that
   prints both the guardrail threshold and the saturation-reject threshold
   so operators can see the headroom in the same line.
2. `MetricsRegistry` sets `guardrail.alert.db=1.0` /
   `guardrail.alert.broker=1.0` and updates the `db_pool.saturation` and
   `broker.saturation` gauges.

A live readout is exposed via `GET /health/concurrency`, which returns
the active profile, any operator-applied overrides, and the most recent
guardrail readings so orchestrators can use it as a readiness gate.

### Tuning Workflow

1. Pick an environment in `APP_ENV`.
2. If a single field needs adjustment (e.g. larger DB pool in a tuned
   staging node), set the individual env var — do **not** fork the
   profile.
3. Watch `/health/concurrency` after each change; if alert gauges stay
   at `0.0` for a representative load window, the change is healthy.
4. If `db_pool.saturation` or `broker.saturation` consistently sits above
   the threshold, scale horizontally (add worker nodes) **before**
   raising any per-node number.