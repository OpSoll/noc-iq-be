# NOC IQ Backend

Backend API and integration layer for the NOC IQ system.

This repository sits in the middle of the 3-repo architecture:

- `noc-iq-fe` -> frontend
- `noc-iq-be` -> backend and integration layer
- `noc-iq-contracts` -> Soroban smart contracts

System flow:

`User -> FE -> BE -> Contracts -> BE -> FE`

Important rule:

- the frontend does not call contracts directly
- the backend is the bridge between UI and contract execution

## Overview

`noc-iq-be` is a FastAPI application responsible for:

- managing outages
- computing and storing SLA results
- exposing aggregation and audit endpoints
- acting as the future bridge to Soroban contracts

As of the current stabilized baseline, the backend is strongest in the outage and SLA domains. Other domains are now routed, but not all of them are equally production-ready.

## Current Stack

- Python
- FastAPI
- SQLAlchemy
- PostgreSQL
- Alembic
- Pydantic Settings
- Celery
- HTTPX

Dependencies are declared in [requirements.txt](/Users/m-ibinola/Documents/personal/semilore/noc-iq-be/requirements.txt).

## Active Runtime Surface

The app entrypoint is [app/main.py](/Users/m-ibinola/Documents/personal/semilore/noc-iq-be/app/main.py).

Current active routes are wired through [app/api/v1/router.py](/Users/m-ibinola/Documents/personal/semilore/noc-iq-be/app/api/v1/router.py):

- `/health`
- `/api/v1/audit`
- `/api/v1/jobs`
- `/api/v1/outages`
- `/api/v1/sla`
- `/api/v1/sla/disputes`
- `/api/v1/auth`
- `/api/v1/payments`
- `/api/v1/webhooks`
- `/api/v1/wallets`

Module maturity on the routed runtime:

- strongest and most integration-focused: `outages`, `sla`, `audit`
- active and functional with lighter implementations: `auth`, `payments`, `wallets`
- active but operationally dependent on database or worker infrastructure: `jobs`, `webhooks`, `sla disputes`

Dormant or contributor-only paths:

- `app/services/outage_store.py` is a legacy helper and not part of the routed runtime
- local task and webhook support still depend on optional infrastructure like Redis and Celery for full behavior
- the backend contains both a local SLA execution path and a contract adapter path; `CONTRACT_EXECUTION_MODE` determines which bridge is active at runtime

## Outage And SLA Flow

The current working backend flow is:

1. create or update an outage
2. resolve the outage with `mttr_minutes`
3. calculate SLA in the backend
4. persist the resulting SLA record
5. return the outage and SLA result to the frontend

Key files:

- `app/api/v1/endpoints/outages.py`
- `app/api/v1/endpoints/sla.py`
- `app/repositories/outage_repository.py`
- `app/repositories/sla_repository.py`
- `app/services/sla/sla_calculator.py`
- `app/services/sla/config.py`

The backend now includes both a local SLA calculator and a contract adapter surface. By default it uses the local adapter mode, but the runtime is structured so contract-backed execution can be enabled through configuration.

## Project Structure

```text
noc-iq-be/
├── alembic/                 # database migration config and versions
├── app/
│   ├── api/v1/endpoints/    # FastAPI route handlers
│   ├── core/                # settings and application config
│   ├── db/                  # SQLAlchemy base and session setup
│   ├── models/              # Pydantic and ORM models
│   ├── repositories/        # DB access layer
│   ├── services/            # domain logic and utilities
│   ├── tasks/               # Celery-related modules
│   └── utils/               # helpers such as exporters
├── docs/                    # project and integration context
├── requirements.txt
└── README.md
```

## Local Setup

### Prerequisites

- Python 3.11+ recommended
- PostgreSQL
- pip
- virtual environment support

### Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Configure Environment

Create a `.env` file in the repo root.

Common settings used by the app:

```env
PROJECT_NAME=NOCIQ API
VERSION=1.0.0
DEBUG=true
DATABASE_URL=postgresql://postgres:password@localhost:5432/nociq
ALLOWED_ORIGINS=["http://localhost:3000","http://localhost:3001"]
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/0
```

### Run Migrations

```bash
alembic upgrade head
```

### Start The API

```bash
uvicorn app.main:app --reload
```

The backend will be available at:

- `http://localhost:8000`
- Swagger docs: `http://localhost:8000/docs`
- Health check: `http://localhost:8000/health`

## Verification Notes

As of the latest stabilization pass:

- Python modules compile cleanly
- `app.main` imports successfully
- `/health` returns `200`

To exercise outage and SLA routes meaningfully, you still need a reachable PostgreSQL instance because those routes depend on the database layer.

## Current Limitations

This backend is stabilized, but not feature-complete.

Examples:

- `auth` and `wallets` are active but currently backed by lightweight in-memory stores rather than durable identity infrastructure
- `jobs` and `webhooks` are routed, but they rely on optional worker infrastructure to be fully operational outside eager or local modes
- the contract path exists, but the default runtime still favors the local adapter mode
- documentation and contributor expectations should follow the routed API surface, not every helper or legacy module under `app/services`

## Related Repositories

- `noc-iq-fe` -> frontend application
- `noc-iq-contracts` -> Soroban smart contracts
