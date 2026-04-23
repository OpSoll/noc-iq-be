from datetime import datetime
from typing import Any, Optional
from sqlalchemy.orm import Session
from app.models.orm.audit_log import AuditLogORM
from app.db.session import SessionLocal

class AuditLogService:
    def __init__(self, db_session_factory=None):
        self.db_session_factory = db_session_factory or SessionLocal

    def log_event(
        self,
        db: Session,
        event_type: str,
        email: Optional[str] = None,
        details: Optional[dict[str, Any]] = None
    ) -> None:
        """
        Records a structured audit event.
        Ensures sensitive data like passwords or tokens are NOT leaked into details.
        """
        # Sanitization: prevent leaking common sensitive keys
        safe_details = details.copy() if details else {}
        sensitive_keys = {"password", "token", "access_token", "refresh_token", "secret"}
        for key in sensitive_keys:
            if key in safe_details:
                safe_details[key] = "[REDACTED]"

        audit_entry = AuditLogORM(
            event_type=event_type,
            email=email,
            details=safe_details,
            created_at=datetime.utcnow()
        )
        db.add(audit_entry)
        db.commit()

    def log(self, event_type: str, details: Optional[dict[str, Any]] = None) -> None:
        """
        Simplified log method for compatibility with existing code.
        Uses its own session if not provided.
        """
        with self.db_session_factory() as db:
            self.log_event(db, event_type, details=details)

# Create a singleton instance for common use
audit_log = AuditLogService()
