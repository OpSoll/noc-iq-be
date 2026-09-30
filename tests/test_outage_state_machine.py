"""Unit tests for the outage lifecycle state machine (Issue #664)."""
from datetime import datetime

import pytest

from app.core.outage_state_machine import (
    ACTIVE,
    DRAFT,
    INVESTIGATING,
    OPEN,
    RESOLVED,
    InvalidStateTransition,
    OutageStateMachine,
    OutageStateTransitioned,
    clear_transition_listeners,
    subscribe_transition_listener,
    unsubscribe_transition_listener,
)
from app.repositories.outage_repository import OutageRepository


# Each entry is (from_status, to_status) for a legal lifecycle move.
VALID_PATHS = [
    (DRAFT, INVESTIGATING),
    (DRAFT, OPEN),
    (OPEN, INVESTIGATING),
    (OPEN, ACTIVE),
    (OPEN, RESOLVED),
    (INVESTIGATING, ACTIVE),
    (INVESTIGATING, RESOLVED),
    (INVESTIGATING, OPEN),
    (ACTIVE, RESOLVED),
    (RESOLVED, RESOLVED),  # idempotent
    (OPEN, OPEN),  # idempotent
    (DRAFT, DRAFT),  # idempotent
]

# Each entry is (from_status, to_status) for an illegal lifecycle move.
INVALID_PATHS = [
    (DRAFT, ACTIVE),
    (DRAFT, RESOLVED),
    (OPEN, DRAFT),
    (INVESTIGATING, DRAFT),
    (ACTIVE, DRAFT),
    (ACTIVE, OPEN),
    (ACTIVE, INVESTIGATING),
    (RESOLVED, OPEN),
    (RESOLVED, INVESTIGATING),
    (RESOLVED, ACTIVE),
    (RESOLVED, DRAFT),
]


@pytest.fixture(autouse=True)
def _reset_listeners():
    clear_transition_listeners()
    yield
    clear_transition_listeners()


@pytest.mark.parametrize("from_status,to_status", VALID_PATHS)
def test_can_transition_allows_valid_paths(from_status, to_status):
    assert OutageStateMachine.can_transition(from_status, to_status) is True


@pytest.mark.parametrize("from_status,to_status", INVALID_PATHS)
def test_can_transition_rejects_invalid_paths(from_status, to_status):
    assert OutageStateMachine.can_transition(from_status, to_status) is False


@pytest.mark.parametrize("from_status,to_status", VALID_PATHS)
def test_validate_transition_accepts_valid_paths(from_status, to_status):
    # Must not raise.
    OutageStateMachine.validate_transition(from_status, to_status)


@pytest.mark.parametrize("from_status,to_status", INVALID_PATHS)
def test_validate_transition_rejects_invalid_paths(from_status, to_status):
    with pytest.raises(InvalidStateTransition) as exc_info:
        OutageStateMachine.validate_transition(from_status, to_status)

    exc = exc_info.value
    assert exc.from_status == from_status
    assert exc.to_status == to_status
    assert isinstance(exc, ValueError)  # backward compatible


def test_rejects_draft_to_resolved_without_investigating():
    """Acceptance criterion: DRAFT -> RESOLVED must be rejected."""
    with pytest.raises(InvalidStateTransition):
        OutageStateMachine.transition("out-1", DRAFT, RESOLVED)


def test_draft_reaches_resolved_through_investigating():
    """DRAFT -> INVESTIGATING -> RESOLVED is the legal path."""
    OutageStateMachine.validate_transition(DRAFT, INVESTIGATING)
    OutageStateMachine.validate_transition(INVESTIGATING, RESOLVED)


def test_invalid_transition_exposes_structured_details():
    with pytest.raises(InvalidStateTransition) as exc_info:
        OutageStateMachine.validate_transition(ACTIVE, OPEN)

    payload = exc_info.value.to_dict()
    assert payload["error"] == "invalid_state_transition"
    assert payload["from_status"] == ACTIVE
    assert payload["to_status"] == OPEN
    assert payload["valid_transitions"] == [RESOLVED]


def test_unknown_source_status_is_rejected():
    with pytest.raises(InvalidStateTransition) as exc_info:
        OutageStateMachine.validate_transition("pending", OPEN)
    assert exc_info.value.from_status == "pending"


def test_unknown_target_status_is_rejected():
    with pytest.raises(InvalidStateTransition):
        OutageStateMachine.validate_transition(OPEN, "resovled")


def test_get_valid_next_states_returns_copy():
    states = OutageStateMachine.get_valid_next_states(OPEN)
    assert states == {INVESTIGATING, ACTIVE, RESOLVED}

    states.add("tampered")
    assert "tampered" not in OutageStateMachine.get_valid_next_states(OPEN)


def test_get_valid_next_states_rejects_unknown_status():
    with pytest.raises(InvalidStateTransition):
        OutageStateMachine.get_valid_next_states("nope")


def test_transition_emits_outage_state_transitioned_event():
    received = []
    subscribe_transition_listener(received.append)

    event = OutageStateMachine.transition("out-42", OPEN, RESOLVED)

    assert isinstance(event, OutageStateTransitioned)
    assert event.outage_id == "out-42"
    assert event.from_status == OPEN
    assert event.to_status == RESOLVED
    assert isinstance(event.occurred_at, datetime)
    assert received == [event]
    assert event.to_dict()["to_status"] == RESOLVED


def test_invalid_transition_emits_no_event():
    received = []
    subscribe_transition_listener(received.append)

    with pytest.raises(InvalidStateTransition):
        OutageStateMachine.transition("out-43", DRAFT, RESOLVED)

    assert received == []


def test_unsubscribe_stops_delivery():
    received = []
    listener = subscribe_transition_listener(received.append)
    unsubscribe_transition_listener(listener)

    OutageStateMachine.transition("out-44", OPEN, INVESTIGATING)
    assert received == []


def test_listener_exception_is_isolated():
    received = []

    def boom(_event):
        raise RuntimeError("listener failure")

    subscribe_transition_listener(boom)
    subscribe_transition_listener(received.append)

    event = OutageStateMachine.transition("out-45", OPEN, ACTIVE)
    assert received == [event]


def test_audit_failure_does_not_block_transition(monkeypatch):
    def _boom(*_args, **_kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr("app.core.outage_state_machine.audit_log.log", _boom)

    event = OutageStateMachine.transition("out-46", OPEN, RESOLVED)
    assert event.to_status == RESOLVED


def test_repository_delegates_to_state_machine():
    # Valid
    OutageRepository.validate_status_transition(OPEN, RESOLVED)
    OutageRepository.validate_status_transition(DRAFT, INVESTIGATING)

    # Invalid
    with pytest.raises(InvalidStateTransition, match="Invalid status transition"):
        OutageRepository.validate_status_transition(RESOLVED, OPEN)


def test_patch_endpoint_returns_structured_400_on_invalid_transition(client):
    """The API responds 400 with InvalidStateTransition details."""
    from datetime import datetime, timezone
    import uuid

    from app.models import OutageCreate
    from app.models.enums import OutageStatus, Severity

    from app.db.session import SessionLocal
    from app.repositories.outage_repository import OutageRepository

    outage_id = f"sm-{uuid.uuid4().hex[:8]}"
    session = SessionLocal()
    try:
        OutageRepository(session).create(
            OutageCreate(
                id=outage_id,
                site_name=f"State Machine {outage_id}",
                severity=Severity.critical,
                status=OutageStatus.resolved,
                detected_at=datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc),
                description="state machine API test",
                affected_services=["service1"],
            )
        )
        session.commit()
    finally:
        session.close()

    response = client.patch(
        f"/api/v1/outages/{outage_id}",
        json={"status": "open"},
        headers={"Authorization": "Bearer test-engineer-token"},
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["error"] == "invalid_state_transition"
    assert detail["from_status"] == RESOLVED
    assert detail["to_status"] == OPEN
