"""V2 health-check endpoint (#414)."""

from datetime import datetime

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def v2_health() -> dict:
    return {
        "status": "ok",
        "version": "v2",
        "timestamp": datetime.utcnow().isoformat(),
    }
