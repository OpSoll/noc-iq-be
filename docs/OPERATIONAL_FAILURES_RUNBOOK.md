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

## Backup verification cadence

`scripts/verify_db_backup.sh` runs a `pg_dump`, restores it into a temporary
database, and compares row counts for the core tables. Run it as a drill; it is
not a replacement for the production backup job.

**Ownership and cadence**

- The deployment owner for each environment (the person or team recorded as
  owning that deployment) runs the drill, or delegates it by name in the
  evidence record below.
- Suggested cadence: at least once per release cycle and before any schema
  migration is applied to production. Deployment owners may tighten this.

**How to run** (inject the connection string from your secrets manager or
shell environment; never paste real credentials into tickets, shell history
shared with others, or this repository):

```bash
export DATABASE_URL   # already set from the secrets manager; do not echo it
./scripts/verify_db_backup.sh
```

Optional overrides: `RESTORE_DB_NAME`, `BACKUP_TABLES`, `BACKUP_DIR`. The
script exits non-zero on any row-count mismatch; treat that as a failed drill.

**Recording restore evidence**

Add an entry to `docs/operations/incident-notes.md` (the same log used in
Succession below) containing:

- Date/time (UTC) and environment
- Who ran the drill
- The script's `[verify_db_backup]` log output (confirm no connection string or
  password appears before pasting)
- Result: pass, or fail with the mismatching table and follow-up action
