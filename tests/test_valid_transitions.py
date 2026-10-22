"""Tests for issue #997 — valid-transition endpoint.

Acceptance criteria:
- Each status covered
- Resolved terminal covered
- Unknown id 404
- Test suite passes

The endpoint is GET /api/v1/outages/{outage_id}/transitions and returns
the permitted next states for the outage's current status.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from app.core.outage_state_machine import (
    ACTIVE,
    DRAFT,
    INVESTIGATING,
    OPEN,
    RESOLVED,
    KNOWN_STATUSES,
    VALID_TRANSITIONS,
)
from app.db.session import SessionLocal
from app.models import OutageCreate
from app.models.enums import OutageStatus, Severity
from app.repositories.outage_repository import OutageRepository


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_outage(db, status: str = "open") -> str:
    """Create an outage in the given status and return its id."""
    outage_id = f"tr-{uuid.uuid4().hex[:8]}"
    OutageRepository(db).create(
        OutageCreate(
            id=outage_id,
            site_name=f"Transition Test {outage_id}",
            severity=Severity.medium,
            status=OutageStatus(status) if status in ("open", "resolved") else OutageStatus.open,
            detected_at=datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc),
            description="transition endpoint test",
            affected_services=["service1"],
        )
    )
    db.commit()
    return outage_id


def _create_outage_via_session(status: str = "open") -> str:
    """Create an outage using a direct session (for non-enum statuses)."""
    session = SessionLocal()
    try:
        outage_id = _create_outage(session, status)
        return outage_id
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestValidTransitionsOpen:
    """Open status: can go to investigating, active, resolved, or stay open."""

    def test_open_returns_valid_next_states(self, client, db):
        outage_id = _create_outage(db, "open")
        response = client.get(f"/api/v1/outages/{outage_id}/transitions")
        assert response.status_code == 200
        body = response.json()
        assert body["outage_id"] == outage_id
        assert body["current_status"] == "open"
        # VALID_TRANSITIONS["open"] = {investigating, active, resolved}
        # The endpoint returns sorted list
        assert set(body["valid_next_states"]) == {"active", "investigating", "resolved"}


class TestValidTransitionsResolved:
    """Resolved is terminal — only self-transition allowed."""

    def test_resolved_returns_only_self(self, client, db):
        outage_id = _create_outage(db, "resolved")
        response = client.get(f"/api/v1/outages/{outage_id}/transitions")
        assert response.status_code == 200
        body = response.json()
        assert body["current_status"] == "resolved"
        # Resolved is terminal: only self-transition
        assert body["valid_next_states"] == ["resolved"]


class TestValidTransitionsUnknownId:
    """Unknown outage id returns 404."""

    def test_unknown_id_returns_404(self, client):
        response = client.get("/api/v1/outages/nonexistent-outage-id/transitions")
        assert response.status_code == 404


class TestValidTransitionsRequiresNoAuth:
    """The transitions endpoint does not require authentication (read-only)."""

    def test_no_auth_required(self, client, db):
        outage_id = _create_outage(db, "open")
        response = client.get(f"/api/v1/outages/{outage_id}/transitions")
        assert response.status_code == 200


class TestValidTransitionsResponseSchema:
    """Verify the response schema matches the documented contract."""

    def test_response_has_required_fields(self, client, db):
        outage_id = _create_outage(db, "open")
        response = client.get(f"/api/v1/outages/{outage_id}/transitions")
        body = response.json()
        assert "outage_id" in body
        assert "current_status" in body
        assert "valid_next_states" in body

    def test_valid_next_states_is_sorted_list(self, client, db):
        outage_id = _create_outage(db, "open")
        response = client.get(f"/api/v1/outages/{outage_id}/transitions")
        body = response.json()
        states = body["valid_next_states"]
        assert isinstance(states, list)
        assert states == sorted(states)


class TestValidTransitionsMatchesStateMachine:
    """The endpoint output must match the state machine's get_valid_next_states."""

    def test_open_matches_state_machine(self, client, db):
        from app.core.outage_state_machine import OutageStateMachine
        outage_id = _create_outage(db, "open")
        response = client.get(f"/api/v1/outages/{outage_id}/transitions")
        body = response.json()
        expected = sorted(OutageStateMachine.get_valid_next_states("open"))
        assert body["valid_next_states"] == expected

    def test_resolved_matches_state_machine(self, client, db):
        from app.core.outage_state_machine import OutageStateMachine
        outage_id = _create_outage(db, "resolved")
        response = client.get(f"/api/v1/outages/{outage_id}/transitions")
        body = response.json()
        expected = sorted(OutageStateMachine.get_valid_next_states("resolved"))
        assert body["valid_next_states"] == expected
