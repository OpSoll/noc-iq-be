import pytest

from app.repositories.outage_repository import OutageRepository


def test_status_transition_open_to_resolved_allowed():
    OutageRepository.validate_status_transition("open", "resolved")


def test_status_transition_resolved_to_open_disallowed():
    with pytest.raises(ValueError, match="Invalid status transition"):
        OutageRepository.validate_status_transition("resolved", "open")
