from fastapi import APIRouter

router = APIRouter()

@router.get("/ping")
def payments_ping():
    return {"message": "payments ok"}
