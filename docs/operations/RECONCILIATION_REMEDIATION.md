# Analytics Reconciliation Remediation Playbook

When the automated SLA reconciliation task flags an active discrepancy, it implies data delivery streams dropped payloads during high concurrent write windows.

## Immediate Resolution Steps

### Step 1: Analyze the Alarm Footprint
Locate the specific time interval boundaries specified inside the log event payload (`window`).

### Step 2: Trigger the Force Re-Sync Event Handler
Invoke the backend administration command utility to trigger an isolated point-of-time catch-up script over the drifted window:
```bash
python manage.py analytics:force_resync --start="2026-06-29T15:00:00" --end="2026-06-29T16:00:00"