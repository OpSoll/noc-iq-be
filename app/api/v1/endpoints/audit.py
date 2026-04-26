from fastapi import APIRouter, Depends
from app.services.audit_log import audit_log
from app.core.security import require_admin

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("")
def get_audit_log(current_user=Depends(require_admin)):
    return audit_log.list()