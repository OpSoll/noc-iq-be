from fastapi import APIRouter
from app.services.audit_log import audit_log

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("")
def get_audit_log():
    return audit_log.list()