from fastapi import APIRouter, Depends, HTTPException
from app.services.sla_service import SLAService
from pydantic import BaseModel

router = APIRouter()
sla_service = SLAService()

class ConfigChangeReq(BaseModel):
    config_id: int

@router.post("/recalculate")
def recalculate_sla_endpoint(req: ConfigChangeReq):
    success = sla_service.recalculate_sla(req.config_id)
    if not success:
        raise HTTPException(status_code=500, detail="SLA recalculation failed")
    return {"status": "success", "config_id": req.config_id}
