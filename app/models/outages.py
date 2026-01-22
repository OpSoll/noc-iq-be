from fastapi import APIRouter
from typing import List
from datetime import datetime

from app.models import Outage

router = APIRouter()


@router.get("/", response_model=List[Outage])
def list_outages():
    # Temporary stub data
    return [
        Outage(
            id="OUT001",
            site_name="Cell Tower Alpha",
            site_id="SITE123",
            severity="critical",
            status="active",
            detected_at=datetime.utcnow(),
            description="Total signal loss",
            affected_services=["4G", "5G"],
        )
    ]