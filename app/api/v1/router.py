from fastapi import APIRouter
from app.api.v1.endpoints import audit

from app.api.v1.endpoints import (
    analytics,
    auth,
    health,
    jobs,
    metrics,
    outages,
    sla,
    sla_dispute,
    payments,
    transactions,
    webhooks,
    wallets,
    metrics,
)

from app.api.endpoints import (
    changelog,
    dispute,
    idempotency,
    logging as logging_ep,
    outage_bulk,
    readiness,
    site_hierarchy,
)

api_router = APIRouter()

api_router.include_router(health.router)
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(audit.router)
api_router.include_router(jobs.router)
api_router.include_router(metrics.router)
api_router.include_router(outages.router, prefix="/outages", tags=["outages"])
api_router.include_router(sla.router, prefix="/sla", tags=["sla"])
api_router.include_router(sla_dispute.router, prefix="/sla", tags=["sla-disputes"])
api_router.include_router(dispute.router, prefix="/disputes", tags=["disputes"])
api_router.include_router(payments.router, prefix="/payments", tags=["payments"])
api_router.include_router(transactions.router, prefix="/transactions", tags=["transactions"])
api_router.include_router(webhooks.router)
api_router.include_router(wallets.router, prefix="/wallets", tags=["wallets"])
api_router.include_router(metrics.router, prefix="/metrics", tags=["metrics"])
api_router.include_router(metrics.router, prefix="/health", tags=["health"])
api_router.include_router(changelog.router, prefix="/changelog", tags=["changelog"])
api_router.include_router(idempotency.router, prefix="/idempotency", tags=["idempotency"])
api_router.include_router(logging_ep.router, prefix="/logging", tags=["logging"])
api_router.include_router(readiness.router, prefix="/system", tags=["system"])
api_router.include_router(site_hierarchy.router, prefix="/site-hierarchy", tags=["site-hierarchy"])
api_router.include_router(analytics.router, prefix="/analytics", tags=["analytics"])
