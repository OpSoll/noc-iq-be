# NOC IQ Backend (noc-iq-be) – Codex Context

## Overview

This repository powers the backend API for NOC IQ, a network operations intelligence platform.

It is responsible for:
- managing outages and RCA
- calculating SLA performance
- triggering blockchain-based payments (Stellar)
- serving analytics and reports
- handling authentication and wallet management

The backend is built with FastAPI and integrates with Stellar smart contracts for automated payments.

Reference: :contentReference[oaicite:0]{index=0}

---

## Tech Stack

- Framework: FastAPI
- Language: Python (3.9+)
- Database: Firestore
- Auth: Firebase + JWT
- Blockchain: Stellar SDK + Soroban smart contracts
- Validation: Pydantic
- Async: async/await

---

## Core Domains

### 1. Outage Management

Responsible for:
- creating outages
- updating outage status
- tracking resolution
- storing metadata (location, services, subscribers)

Key endpoints:
- GET /outages
- POST /outages
- PUT /outages/{id}

---

### 2. SLA System

Core business logic.

Responsible for:
- calculating MTTR
- determining SLA compliance
- triggering penalties or rewards
- invoking smart contracts

Key endpoints:
- GET /sla/status/{outage_id}
- POST /sla/calculate
- POST /sla/execute-payment

Important:
- SLA depends on severity thresholds
- Payment logic is tightly coupled with SLA

---

### 3. Payments (Stellar Integration)

Responsible for:
- executing SLA payments
- tracking transaction status
- storing transaction history

Key endpoints:
- POST /payments/process-sla
- GET /payments/history

Flow:
Outage → SLA Calculation → Smart Contract → Payment → Record stored

---

### 4. Wallet Management

Responsible for:
- creating Stellar wallets
- retrieving balances
- linking wallets to users

Key endpoints:
- POST /wallets/create
- GET /wallets/{user_id}
- GET /wallets/{address}/balance

Important constraint:
- private keys are NEVER returned via API

---

### 5. Analytics

Responsible for:
- MTTR calculations
- SLA compliance metrics
- payment analytics

Key endpoints:
- GET /analytics/mttr
- GET /analytics/payments

---

### 6. Authentication

Responsible for:
- login
- registration
- JWT issuance

Key endpoints:
- POST /auth/login
- POST /auth/register

---

## Architecture

### Layered Structure

- API Layer → routes (FastAPI endpoints)
- Service Layer → business logic
- Repository Layer → Firestore interaction
- External Layer → Stellar + Smart Contracts

---

## Important Business Flows

### SLA Payment Flow

1. Outage created
2. Outage resolved
3. MTTR calculated
4. SLA evaluated
5. Smart contract invoked
6. Payment executed on Stellar
7. Transaction stored in DB

Reference: :contentReference[oaicite:1]{index=1}

---

## Constraints & Rules

- All monetary actions must go through SLA system
- Payments must be idempotent
- Wallet operations must be secure (no private key exposure)
- SLA must be deterministic and reproducible
- API responses must follow consistent structure

---

## Known Gaps (Areas to Generate Issues)

Codex should focus on generating issues for:

### Backend Improvements
- validation consistency across endpoints
- error handling standardization
- pagination consistency
- DTO/schema refinement

### SLA System
- edge cases in MTTR calculation
- SLA simulation endpoint
- replay/recalculate SLA
- audit logging

### Payments
- retry logic for failed transactions
- idempotent payment execution
- payment reconciliation
- transaction monitoring

### Wallets
- wallet funding flow
- trustline verification
- wallet status validation

### Analytics
- caching for heavy queries
- aggregation optimization
- dashboard endpoints

### DevOps / Infra
- rate limiting improvements
- logging and observability
- environment config validation
- CI/CD pipelines

---

## Coding Standards

- use async functions for IO operations
- separate routes from business logic
- validate all inputs with Pydantic
- never embed business logic in controllers
- services must be reusable and testable

---

## Issue Generation Rules

When generating issues:

- scope each issue to ONE domain
- include:
  - title
  - description
  - acceptance criteria
  - affected modules/files
  - dependencies (Blocked By)
- prefer small, atomic tasks
- group issues into:
  - foundational
  - feature
  - integration
  - optimization

---

## Cross-Repo Dependencies

This repo depends on:

- noc-iq-fe → consumes API
- noc-iq-contracts → executes SLA logic

Important:
- any change in SLA logic may affect contracts
- any API shape change affects frontend

---

## Goal for Codex

Generate a structured backlog of issues that:

- improves backend reliability
- ensures correctness of SLA + payments
- prepares system for production scale
- maintains clean separation of concerns
