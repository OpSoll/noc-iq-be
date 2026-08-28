"""V2 API router scaffold (#414)."""

from fastapi import APIRouter

from app.api.v2.endpoints.health import router as health_router

api_v2_router = APIRouter()
api_v2_router.include_router(health_router)
