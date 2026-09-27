from datetime import datetime, timezone
from typing import Any, Optional
from sqlalchemy.orm import Session
from app.models.orm.audit_log import AuditLogORM
from app.db.session import SessionLocal
from app.utils.correlation import get_correlation_id


class WalletAuditEvents:
    """Standardized audit event taxonomy for all wallet-related operations."""

    # Creation
    WALLET_CREATED = "wallet.created"
    WALLET_CREATE_FAILED = "wallet.create_failed"

    # Linking
    WALLET_LINKED = "wallet.linked"
    WALLET_LINK_FAILED = "wallet.link_failed"

    # Fetch
    WALLET_FETCHED = "wallet.fetched"
    WALLET_FETCH_FAILED = "wallet.fetch_failed"

    # Status
    WALLET_STATUS_CHECKED = "wallet.status_checked"
    WALLET_STATUS_CHECK_FAILED = "wallet.status_check_failed"

    # Trustline
    WALLET_TRUSTLINE_CHECKED = "wallet.trustline_checked"
    WALLET_TRUSTLINE_CHECK_FAILED = "wallet.trustline_check_failed"

    # Funding state
    WALLET_FUNDING_STATE_CHECKED = "wallet.funding_state_checked"
    WALLET_FUNDING_STATE_CHECK_FAILED = "wallet.funding_state_check_failed"

    # Balance
    WALLET_BALANCE_CHECKED = "wallet.balance_checked"
    WALLET_BALANCE_CHECK_FAILED = "wallet.balance_check_failed"

    # Prefix used by the audit query endpoint to filter all wallet events
    PREFIX = "wallet."


class Secu:
    _SENSITIVE_KEYS = {
        "password",
        "token",
        "access_token",
        "refresh_token",
        "secret",
        "secret_key",
        # Wallet-specific secrets
        "seed",
        "secret_seed",
        "private_key",
        "mnemonic",
    }

    def __init__(self, db_session_factory=None):
        self.db_session_factory = db_session_factory or SessionLocal

    def _sanitize(self, details: Optional[dict[str, Any]]) -> dict[str, Any]:
        """Return a copy of details with all sensitive fields redacted."""
        if not details:
            return {}
        safe = details.copy()
        for key in self._SENSITIVE_KEYS:
            if key in safe:
                safe[key] = "[REDACTED]"
        return safe

    def log_event(
        self,
        db: Session,
        event_type: str,
        email: Optional[str] = None,
        actor_id: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
        correlation_id: Optional[str] = None,
    ) -> None:
        """
        Records a structured audit event with actor attribution and correlation context.

        BE-010 Enhancements:
        - actor_id: Consistent user identifier for cross-event correlation
        - correlation_id: Request correlation ID to link related events
        - Sensitive data sanitization to prevent secret leakage

        Args:
            db: Database session
            event_type: Namespaced event type (e.g., 'wallet.created', 'login_success')
            email: User email (legacy field, kept for backward compatibility)
            actor_id: User ID for consistent actor tracking (preferred over email)
            details: Event details dict (will be sanitized before persistence)
            correlation_id: Request correlation ID (auto-detected from context if omitted)
        """
        if correlation_id is None:
            correlation_id = get_correlation_id()

        audit_entry = AuditLogORM(
            event_type=event_type,
            email=email,
            actor_id=actor_id,
            correlation_id=correlation_id,
            details=self._sanitize(details),
            created_at=datetime.now(timezone.utc),
        )
        db.add(audit_entry)
        db.commit()

    def log(
        self,
        event_type: str,
        details: Optional[dict[str, Any]] = None,
        actor_id: Optional[str] = None,
        email: Optional[str] = None,
    ) -> None:
        """
        Simplified log method for fire-and-forget use cases.
        Opens and closes its own session internally.
        """
        with self.db_session_factory() as db:
            self.log_event(db, event_type, email=email, actor_id=actor_id, details=details)

    def list(
        self,
        event_type_prefix: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """
        Return audit log entries ordered by most recent first.

        Args:
            event_type_prefix: If provided, only return events whose event_type
                               starts with this string (e.g., 'wallet.' returns all
                               wallet events). Case-sensitive.
            limit: Maximum number of records to return.
            offset: Number of records to skip (for pagination).
        """
        with self.db_session_factory() as db:
            query = db.query(AuditLogORM)
            if event_type_prefix:
                query = query.filter(
                    AuditLogORM.event_type.like(f"{event_type_prefix}%")
                )
            rows = (
                query.order_by(AuditLogORM.created_at.desc())
                .offset(offset)
                .limit(limit)
                .all()
            )
            return [
                {
                    "id": r.id,
                    "event_type": r.event_type,
                    "email": r.email,
                    "actor_id": r.actor_id,
                    "correlation_id": r.correlation_id,
                    "details": r.details,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
            ]


# Singleton instance for common use
audit_log = Secu()