from fastapi import APIRouter

router = APIRouter()

@router.get("/ping")
def wallets_ping():
    return {"message": "wallets ok"}
