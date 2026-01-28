from fastapi import APIRouter, HTTPException
from app.models.sla import SLAPreviewRequest
from app.services.sla import SLACalculator
from app.models import SLAResult

router = APIRouter()


@router.get("/calculate", response_model=SLAResult)
def calculate_sla(outage_id: str, severity: str, mttr_minutes: int):
    try:
        return SLACalculator.calculate(
            outage_id=outage_id,
            severity=severity,
            mttr_minutes=mttr_minutes,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/preview")
def preview_sla(payload: SLAPreviewRequest):
    result = calculate_sla(
        outage_id="PREVIEW",
        severity=payload.severity.value,
        mttr_minutes=payload.mttr_minutes,
    )
    return result