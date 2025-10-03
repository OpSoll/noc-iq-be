from fastapi import APIRouter, HTTPException, status
from typing import Dict
from .rca_models import RcaModel 


router = APIRouter(
    prefix="/rca",
    tags=["RCA Management"]
)


db: Dict[str, RcaModel] = {}

@router.post("/{ticket_id}", status_code=status.HTTP_201_CREATED, response_model=RcaModel)
def create_rca(ticket_id: str, rca: RcaModel):
    if ticket_id in db:
        raise HTTPException(status_code=409, detail="RCA already exists.")
    db[ticket_id] = rca
    return rca

@router.get("/{ticket_id}", response_model=RcaModel)
def get_rca(ticket_id: str):
    if ticket_id not in db:
        raise HTTPException(status_code=404, detail="RCA not found.")
    return db[ticket_id]

@router.put("/{ticket_id}", response_model=RcaModel)
def update_rca(ticket_id: str, rca: RcaModel):
    if ticket_id not in db:
        raise HTTPException(status_code=404, detail="RCA not found.")
    db[ticket_id] = rca
    return rca