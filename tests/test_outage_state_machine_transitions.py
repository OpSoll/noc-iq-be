"""Exhaustive transition tests for the outage state machine (#904)."""

import pytest

from app.core.outage_state_machine import (
    ACTIVE,
    DRAFT,
    INVESTIGATING,
    KNOWN_STATUSES,
    OPEN,
    RESOLVED,
    VALID_TRANSITIONS,
    InvalidStateTransition,
    OutageStateMachine,
)

VALID_PAIRS = [
    (source, target)
    for source, targets in VALID_TRANSITIONS.items()
    for target in sorted(targets)
]


@pytest.mark.parametrize("source,target", VALID_PAIRS)
def test_valid_transitions_are_accepted(source, target):
    assert OutageStateMachine.can_transition(source, target) is True
    OutageStateMachine.validate_transition(source, target)


@pytest.mark.parametrize("status", sorted(KNOWN_STATUSES))
def test_self_transitions_are_idempotent(status):
    assert OutageStateMachine.can_transition(status, status) is True
    OutageStateMachine.validate_transition(status, status)


INVALID_PAIRS = [
    (RESOLVED, OPEN),
    (RESOLVED, DRAFT),
    (ACTIVE, OPEN),
    (ACTIVE, DRAFT),
    (DRAFT, RESOLVED),
]


@pytest.mark.parametrize("source,target", INVALID_PAIRS)
def test_invalid_transitions_are_rejected(source, target):
    assert OutageStateMachine.can_transition(source, target) is False
    with pytest.raises(InvalidStateTransition):
        OutageStateMachine.validate_transition(source, target)


def test_unknown_status_is_rejected():
    with pytest.raises(InvalidStateTransition, match="Unknown outage status"):
        OutageStateMachine.validate_transition("unknown", OPEN)


def test_every_known_status_has_an_entry():
    assert set(VALID_TRANSITIONS) == KNOWN_STATUSES


def test_valid_next_states_returns_a_copy():
    states = OutageStateMachine.get_valid_next_states(OPEN)
    states.clear()

    assert OutageStateMachine.get_valid_next_states(OPEN) == VALID_TRANSITIONS[OPEN]
