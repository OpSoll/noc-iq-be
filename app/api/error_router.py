from fastapi import APIRouter, HTTPException

router = APIRouter()

@router.get("/trigger-error")
def trigger_error():
    raise HTTPException(status_code=400, detail="Bad Request Example")
