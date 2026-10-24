# Operational Failures Runbook

This runbook documents how to diagnose and remediate the most common production
failures in NOC IQ. It covers three failure scenarios:

- Database (PostgreSQL) is down
- Redis is down
- Contract RPC is unreachable

## Failure scenarios

### DB down

**Symptoms**

- `GET /health` returns `database_unavailable`.
- `GET /api/v1/jobs/retention-stats` returns HTTP 503.
- Alembic migrations fail to connect or hang.

**Diagnostic steps**

1. Verify the database process is running:
   ```bash
   pg_isready -h <db-host> -p <db-port>
   ```
2. Verify the connection string in `.env` is correct:
   ```bash
   echo $DATABASE_URL
   ```
3. Check the database logs for connection rejections or locks.
4. If Postgres is healthy but connections are refused, check the firewall and
   the app's `DATABASE_URL` host/port.

**Remediation steps**

1. Restart the database service:
   ```bash
   sudo systemctl restart postgresql
   ```
2. If the database was the primary bottleneck, tune `max_connections` and
   `shared_buffers` in `postgresql.conf`, then reload:
   ```bash
   sudo systemctl reload postgresql
   ```
3. Clear any stuck Alembic transactions and re-run migrations from the latest
   head.

### Redis down

**Symptoms**

- Job lease heartbeat reclaims stale leases aggressively.
- Webhook delivery is stuck in `QUEUED` state.
- `/health` shows `redis_unavailable`.

**Diagnostic steps**

1. Verify Redis is reachable:
   ```bash
   redis-cli -h <redis-host> -p <redis-port> ping
   ```
2. Check memory and eviction:
   ```bash
   redis-cli INFO memory
   ```
3. Verify the `REDIS_URL` in `.env` is correct.

**Remediation steps**

1. Restart Redis:
   ```bash
   sudo systemctl restart redis
   ```
2. If Redis is continuously filling memory, lower `maxmemory` and tighten the
   eviction policy.
3. After restarting, run the stale-lease reclamation endpoint to recover stuck
   jobs:
   ```bash
   curl -X POST https://<host>/api/v1/jobs/reclaim-stale-leases
   ```
4. Watch the job status until any quarantined jobs are reprocessed.

### Contract RPC unreachable

**Symptoms**

- Payment and SLA operations fail with `contract_rpc_unreachable`.
- Outages cannot be reconciled with the chain.
- `CONTRACT_EXECUTION_MODE=contract` causes timeouts.

**Diagnostic steps**

1. Verify the RPC endpoint is reachable:
   ```bash
   curl -s <contract-rpc-url>/status
   ```
2. Check the node is synced:
   ```bash
   curl -s <contract-rpc-url>/health
   ```
3. Check the Etherscan/block explorer for the chain status.

**Remediation steps**

1. Fall back to local execution mode if the contract bridge cannot be reached:
   ```bash
   CONTRACT_EXECUTION_MODE=local
   ```
2. Restart the connected chain node or switch to a different RPC provider.
3. Re-run the affected SLA/reconciliation job once the RPC is healthy.
4. Monitor the wallet and outage resync endpoints until the bridge is fully
   functional again.

## Common runbook symbols

| Symbol | Meaning |
|--------|---------|
| `<host>` | The host where NOC IQ is running |
| `<db-host>` | Database host (Postgres) |
| `<redis-host>` | Redis host |
| `<contract-rpc-url>` | Chain RPC endpoint for the contract |

## Succession

When a failure is resolved, log the event to `docs/operations/incident-notes.md`
with the timestamp, root cause, and remediation steps for future reference.
