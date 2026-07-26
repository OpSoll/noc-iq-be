import logging
from typing import Dict, Set

from app.services.audit_log import audit_log

logger = logging.getLogger(__name__)

VALID_TRANSITIONS: Dict[str, Set[str]] = {
    "investigating": {"active", "resolved"},
    "active": {"resolved"},
    "resolved": set(),
}


class OutageStateMachine:
    @staticmethod
    def can_transition(from_status: str, to_status: str) -> bool:
        return to_status in VALID_TRANSITIONS.get(from_status, set())

    @staticmethod
    def validate_transition(from_status: str, to_status: str) -> None:
        if from_status not in VALID_TRANSITIONS:
            raise ValueError(f"Unknown outage status: {from_status}")
        if not OutageStateMachine.can_transition(from_status, to_status):
            raise ValueError(
                f"Invalid transition: {from_status} -> {to_status}. "
                f"Valid transitions from '{from_status}': "
                f"{VALID_TRANSITIONS[from_status] or '(none)'}"
            )

    @staticmethod
    def get_valid_next_states(from_status: str) -> set:
        if from_status not in VALID_TRANSITIONS:
            raise ValueError(f"Unknown outage status: {from_status}")
        return VALID_TRANSITIONS[from_status].copy()

    @staticmethod
    def transition(outage_id: str, from_status: str, to_status: str) -> None:
        OutageStateMachine.validate_transition(from_status, to_status)
        logger.info(
            "Outage %s transition: %s -> %s", outage_id, from_status, to_status
        )
        audit_log.log(
            "outage_status_transition",
            {
                "outage_id": outage_id,
                "from_status": from_status,
                "to_status": to_status,
            },
        )
