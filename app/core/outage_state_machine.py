"""Outage lifecycle state machine.

Issue #664: outage status changes must follow an explicit lifecycle matrix
instead of allowing arbitrary jumps. This module is the single source of truth
for:

* which transitions are legal (``VALID_TRANSITIONS``)
* the ``InvalidStateTransition`` error raised for rejected transitions
* the ``OutageStateTransitioned`` event emitted on every valid transition

The lifecycle is::

    draft -> investigating -> active -> resolved
      |            |
      +-> open <---+

``resolved`` is terminal, and any status may transition to itself
(idempotent re-application of the same status).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Set

from app.services.audit_log import audit_log

logger = logging.getLogger(__name__)


# Lifecycle statuses recognised by the state machine. These are intentionally a
# superset of the persisted ``OutageStatus`` enum because the machine is also
# used to validate contract/frontend payloads before they reach the database.
DRAFT = "draft"
OPEN = "open"
INVESTIGATING = "investigating"
ACTIVE = "active"
RESOLVED = "resolved"

KNOWN_STATUSES: Set[str] = {DRAFT, OPEN, INVESTIGATING, ACTIVE, RESOLVED}

# Explicit transition matrix. ``resolved`` is terminal; self-transitions are
# handled separately as idempotent operations.
VALID_TRANSITIONS: Dict[str, Set[str]] = {
    DRAFT: {INVESTIGATING, OPEN},
    OPEN: {INVESTIGATING, ACTIVE, RESOLVED},
    INVESTIGATING: {ACTIVE, RESOLVED, OPEN},
    ACTIVE: {RESOLVED},
    RESOLVED: {RESOLVED},
}


class InvalidStateTransition(ValueError):
    """Raised when an outage is asked to move along an illegal lifecycle path.

    Subclasses :class:`ValueError` so existing ``except ValueError`` handlers
    keep working, while carrying structured details for API error responses.
    """

    error_code = "invalid_state_transition"

    def __init__(
        self,
        from_status: str,
        to_status: str,
        valid_transitions: Optional[Set[str]] = None,
    ) -> None:
        self.from_status = from_status
        self.to_status = to_status
        self.valid_transitions = sorted(valid_transitions or set())
        if from_status not in KNOWN_STATUSES:
            detail = f"Unknown outage status: {from_status}"
        else:
            detail = (
                f"Invalid status transition: {from_status} -> {to_status}. "
                f"Valid transitions from '{from_status}': "
                f"{self.valid_transitions or '(none)'}"
            )
        super().__init__(detail)

    def to_dict(self) -> Dict[str, object]:
        """Machine-readable error payload returned as the HTTP 400 detail."""
        return {
            "error": self.error_code,
            "from_status": self.from_status,
            "to_status": self.to_status,
            "valid_transitions": self.valid_transitions,
        }


@dataclass(frozen=True)
class OutageStateTransitioned:
    """Event emitted whenever an outage moves between lifecycle states."""

    outage_id: str
    from_status: str
    to_status: str
    occurred_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def to_dict(self) -> Dict[str, object]:
        return {
            "outage_id": self.outage_id,
            "from_status": self.from_status,
            "to_status": self.to_status,
            "occurred_at": self.occurred_at.isoformat(),
        }


TransitionListener = Callable[[OutageStateTransitioned], None]

_listeners: List[TransitionListener] = []


def subscribe_transition_listener(listener: TransitionListener) -> TransitionListener:
    """Register a listener invoked for every valid ``OutageStateTransitioned``."""
    if listener not in _listeners:
        _listeners.append(listener)
    return listener


def unsubscribe_transition_listener(listener: TransitionListener) -> None:
    if listener in _listeners:
        _listeners.remove(listener)


def clear_transition_listeners() -> None:
    _listeners.clear()


def _emit_transition(event: OutageStateTransitioned) -> None:
    for listener in list(_listeners):
        try:
            listener(event)
        except Exception:  # pragma: no cover - listener isolation
            logger.exception(
                "OutageStateTransitioned listener failed for outage %s",
                event.outage_id,
            )


def _normalize(status: str) -> str:
    return str(status).strip().lower()


class OutageStateMachine:
    @staticmethod
    def can_transition(from_status: str, to_status: str) -> bool:
        normalized_from = _normalize(from_status)
        normalized_to = _normalize(to_status)
        if normalized_from == normalized_to:
            # Re-applying the same status is an idempotent no-op transition.
            return normalized_from in KNOWN_STATUSES
        return normalized_to in VALID_TRANSITIONS.get(normalized_from, set())

    @staticmethod
    def validate_transition(from_status: str, to_status: str) -> None:
        normalized_from = _normalize(from_status)
        normalized_to = _normalize(to_status)
        if normalized_from not in KNOWN_STATUSES:
            raise InvalidStateTransition(normalized_from, normalized_to)
        if not OutageStateMachine.can_transition(normalized_from, normalized_to):
            raise InvalidStateTransition(
                normalized_from,
                normalized_to,
                VALID_TRANSITIONS[normalized_from],
            )

    @staticmethod
    def get_valid_next_states(from_status: str) -> set:
        normalized_from = _normalize(from_status)
        if normalized_from not in KNOWN_STATUSES:
            raise InvalidStateTransition(normalized_from, normalized_from)
        return VALID_TRANSITIONS[normalized_from].copy()

    @staticmethod
    def transition(
        outage_id: str,
        from_status: str,
        to_status: str,
        *,
        actor_id: Optional[str] = None,
    ) -> OutageStateTransitioned:
        """Validate and apply a lifecycle transition, emitting an event.

        Raises :class:`InvalidStateTransition` when the move is not permitted.
        """
        OutageStateMachine.validate_transition(from_status, to_status)
        normalized_from = _normalize(from_status)
        normalized_to = _normalize(to_status)

        event = OutageStateTransitioned(
            outage_id=outage_id,
            from_status=normalized_from,
            to_status=normalized_to,
        )
        _emit_transition(event)

        logger.info(
            "Outage %s transition: %s -> %s",
            outage_id,
            normalized_from,
            normalized_to,
        )
        try:
            audit_log.log(
                "outage_state_transitioned",
                {
                    "outage_id": outage_id,
                    "from_status": normalized_from,
                    "to_status": normalized_to,
                    "occurred_at": event.occurred_at.isoformat(),
                },
                actor_id=actor_id,
            )
        except Exception:  # pragma: no cover - audit must never block a transition
            logger.warning(
                "Failed to record audit event for outage %s transition %s -> %s",
                outage_id,
                normalized_from,
                normalized_to,
                exc_info=True,
            )
        return event
