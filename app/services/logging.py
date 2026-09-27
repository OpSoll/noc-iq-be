from sqlalchemy.orm import Session
from app.models.logging import RequestLog
from app.schemas.logging import RequestLogCreate
import copy

class LoggingService:
    def _redact_pii(self, payload: dict) -> dict:
        PII_KEYS = {"email", "password", "ssn", "credit_card", "phone"}
        redacted = copy.deepcopy(payload)
        
        def recurse_redact(data):
            if isinstance(data, dict):
                for k, v in data.items():
                    if k.lower() in PII_KEYS:
                        data[k] = "[REDACTED]"
                    else:
                        recurse_redact(v)
            elif isinstance(data, list):
                for item in data:
                    recurse_redact(item)
                    
        recurse_redact(redacted)
        return redacted

    def log_request(self, db: Session, log_in: RequestLogCreate) -> RequestLog:
        payload = self._redact_pii(log_in.payload) if log_in.payload else None
        db_log = RequestLog(
            method=log_in.method,
            path=log_in.path,
            status_code=log_in.status_code,
            payload=payload
        )
        db.add(db_log)
        db.commit()
        db.refresh(db_log)
        return db_log
