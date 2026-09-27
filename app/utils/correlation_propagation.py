"""Correlation ID propagation utilities for issue #310."""

from app.utils.correlation import get_correlation_id, set_correlation_id


def propagate_to_job_payload(payload: dict) -> dict:
    """Inject current correlation ID into job payload for worker propagation."""
    corr_id = get_correlation_id()
    if corr_id:
        payload["correlation_id"] = corr_id
    return payload


def restore_correlation_from_payload(payload: dict) -> None:
    """Restore correlation ID from job payload in worker context."""
    corr_id = payload.get("correlation_id")
    if corr_id:
        set_correlation_id(corr_id)
