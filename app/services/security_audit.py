"""Schema-versioned audit details + standardized security events. Closes #319, #331."""
from typing import Any, Optional

from app.services.audit_log import audit_log

AUDIT_SCHEMA_VERSION = "1"  # bump on breaking `details` payload shape changes


def versioned_details(details: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Wrap event details with schema_version for migration-safe reads."""
    payload = dict(details or {})
    payload.setdefault("schema_version", AUDIT_SCHEMA_VERSION)
    return payload


def read_schema_version(details: Optional[dict[str, Any]]) -> str:
    """Return an event's schema_version, defaulting to '0' for pre-versioning rows."""
    return "0" if not details else details.get("schema_version", "0")


class SecurityEvents:
    """Standardized event names for high-risk actions and policy violations."""

    POLICY_VIOLATION = "security.policy_violation"
    HIGH_RISK_ACTION = "security.high_risk_action"
    RATE_LIMIT_BREACH = "security.rate_limit_breach"
    PREFIX = "security."


def log_security_event(
    event_type: str,
    severity: str,
    actor_id: Optional[str] = None,
    endpoint: Optional[str] = None,
    details: Optional[dict[str, Any]] = None,
) -> None:
    """Emit a severity-tagged security event onto the shared audit stream."""
    payload = versioned_details(details)
    payload["severity"] = severity
    if endpoint:
        payload["endpoint"] = endpoint
    audit_log.log(event_type, details=payload, actor_id=actor_id)


def query_security_events(limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
    """Return recent security.* events for alerting integrations to consume."""
    return audit_log.list(event_type_prefix=SecurityEvents.PREFIX, limit=limit, offset=offset)
