"""Unit tests for the derived duration_minutes field on the Outage response model.

Acceptance criteria covered:
- Duration is computed for resolved outages (from resolved_at - detected_at)
- Duration is null for unresolved outages
- Value is consistent with the SLA mttr (same timestamp delta, non-negative whole minutes)
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.models.outage import Outage, SLAStatus


def _make_outage(
    *,
    resolved: bool = True,
    duration_minutes: int = 45,
    status: str | None = None,
) -> Outage:
    detected = datetime(2023, 10, 1, 12, 0, 0, tzinfo=timezone.utc)
    resolved_at = detected + timedelta(minutes=duration_minutes) if resolved else None
    return Outage(
        id="outage-001",
        site_name="Site A",
        site_id="site_1",
        severity="high",
        status=status or ("resolved" if resolved else "open"),
        detected_at=detected,
        resolved_at=resolved_at,
        description="Fiber cut",
        affected_services=["4G"],
        affected_subscribers=100,
        assigned_to=None,
        created_by="user1",
        location=None,
        sla_status=SLAStatus(
            status="met",
            mttr_minutes=duration_minutes if resolved else None,
            threshold_minutes=60,
            time_remaining_minutes=15 if resolved else None,
        )
        if resolved
        else None,
        deleted_at=None,
    )


class TestOutageDurationMinutes:
    def test_duration_computed_for_resolved_outage(self):
        outage = _make_outage(resolved=True, duration_minutes=45)
        assert outage.duration_minutes == 45
        # Consistent with SLA mttr
        assert outage.sla_status is not None
        assert outage.duration_minutes == outage.sla_status.mttr_minutes

    def test_duration_null_for_unresolved_outage(self):
        outage = _make_outage(resolved=False)
        assert outage.resolved_at is None
        assert outage.duration_minutes is None

    def test_duration_non_negative_when_resolved_before_detected(self):
        """Guard against inverted timestamps producing a negative duration."""
        detected = datetime(2023, 10, 1, 12, 0, 0, tzinfo=timezone.utc)
        resolved_at = detected - timedelta(minutes=10)
        outage = Outage(
            id="outage-neg",
            site_name="Site B",
            severity="medium",
            status="resolved",
            detected_at=detected,
            resolved_at=resolved_at,
            description="Clock skew case",
            affected_services=["5G"],
        )
        assert outage.duration_minutes == 0

    def test_duration_floors_to_whole_minutes(self):
        detected = datetime(2023, 10, 1, 12, 0, 0, tzinfo=timezone.utc)
        # 45 minutes and 30 seconds → floors to 45
        resolved_at = detected + timedelta(minutes=45, seconds=30)
        outage = Outage(
            id="outage-floor",
            site_name="Site C",
            severity="low",
            status="resolved",
            detected_at=detected,
            resolved_at=resolved_at,
            description="Partial minute",
            affected_services=["4G"],
        )
        assert outage.duration_minutes == 45

    def test_duration_serializes_in_model_dump(self):
        outage = _make_outage(resolved=True, duration_minutes=30)
        data = outage.model_dump()
        assert "duration_minutes" in data
        assert data["duration_minutes"] == 30

        open_outage = _make_outage(resolved=False)
        open_data = open_outage.model_dump()
        assert open_data["duration_minutes"] is None
