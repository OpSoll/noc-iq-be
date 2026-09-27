from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session
from app.api import deps
from app.schemas.readiness import ReadinessResponse
from app.services.readiness import ReadinessService

router = APIRouter()
service = ReadinessService()

@router.get("/ready", response_model=ReadinessResponse)
def check_readiness(
    response: Response,
    db: Session = Depends(deps.get_db),
):
    """
    Detailed readiness probe exposing dependency health.
    """
    result = service.check_dependencies(db)
    if result.status != "ok":
        response.status_code = 503
    return result
