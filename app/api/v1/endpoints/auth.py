from fastapi import APIRouter

router = APIRouter()

@router.get("/ping")
def auth_ping():
    return {"message": "auth ok"}
