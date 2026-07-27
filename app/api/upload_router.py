from fastapi import APIRouter, Depends, Request, UploadFile, File
from app.core.guardrails import check_payload_size
from pydantic import BaseModel

router = APIRouter()

class JsonPayload(BaseModel):
    data: str

@router.post("/upload/json", dependencies=[Depends(check_payload_size)])
async def upload_json(payload: JsonPayload):
    return {"status": "success", "size": len(payload.data)}

@router.post("/upload/multipart", dependencies=[Depends(check_payload_size)])
async def upload_multipart(file: UploadFile = File(...)):
    return {"status": "success", "filename": file.filename}
