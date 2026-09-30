Current Stack
Python
FastAPI
SQLAlchemy
PostgreSQL
Alembic
Pydantic Settings
Celery
HTTPX
Dependencies are declared in requirements.txt.

Active Runtime Surface
The app entrypoint is app/main.py.

Current active routes are wired through app/api/v1/router.py:

/health
/api/v1/health/detailed
/metrics
/api/v1/audit
/api/v1/jobs
/api/v1/outages
/api/v1/sla
/api/v1/sla/disputes
/api/v1/auth
/api/v1/payments
/api/v1/webhooks
/api/v1/wallets
Module maturity on the routed runtime:

strongest and most integration-focused: outages, sla, audit
active and functional with lighter implementations: auth, payments, wallets
active but operationally dependent on database or worker infrastructure: jobs, webhooks, sla disputes
Dormant or contributor-only paths:

app/services/outage_store.py is a legacy helper and not part of the routed runtime
local task and webhook support still depend on optional infrastructure like Redis and Celery for full behavior
the backend contains both a local SLA execution path and a contract adapter path; CONTRACT_EXECUTION_MODE determines which bridge is active at runtime
