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

As of the current stabilized baseline, the backend is strongest in the outage and SLA domains. Some other domains exist in the codebase but are still placeholder or partially wired.

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
- `/api/v1/outages`
- `/api/v1/sla`
- `/api/v1/auth`
- `/api/v1/payments`
- `/api/v1/wallets`

Important nuance:

- `outages` and `sla` are the most implemented domains
- `payments`, `wallets`, and `auth` currently expose minimal placeholder endpoints

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

Right now, SLA execution is still local backend logic. The repo is prepared to be the contract bridge, but a full contract-backed adapter is not yet the primary runtime path.

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

- `payments` is still a placeholder surface
- `wallets` is still a placeholder surface
- `auth` is still a placeholder surface
- some operational modules such as jobs, webhooks, and disputes exist in the repo but are not part of the main routed runtime path
- the live SLA path is currently backend-local logic rather than a full Soroban invocation path

## Related Repositories

- `noc-iq-fe` -> frontend application
- `noc-iq-contracts` -> Soroban smart contracts
