from fastapi import APIRouter

router = APIRouter()

@router.get("/ping")
def sla_ping():
    return {"message": "sla ok"}
