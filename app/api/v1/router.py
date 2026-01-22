from fastapi import APIRouter

from app.api.v1.endpoints import (
    auth,
    outages,
    sla,
    payments,
    wallets,
)

api_router = APIRouter()

api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(outages.router, prefix="/outages", tags=["outages"])
api_router.include_router(sla.router, prefix="/sla", tags=["sla"])
api_router.include_router(payments.router, prefix="/payments", tags=["payments"])
api_router.include_router(wallets.router, prefix="/wallets", tags=["wallets"])
