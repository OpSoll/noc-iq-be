"""
Tests for Issue #374 (BE-W5-113): Audit-backed analytics quality checks.

Verifies that check_event_coverage() correctly:
- Reports all classes as "ok" when all are well-represented.
- Flags classes as "critical" (missing) when absent from the audit stream.
- Flags classes as "warning" (underrepresented) when count < threshold.
- Produces a complete, machine-readable EventCoverageReport.
"""
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from app.services.audit_log import (
    AuditLogService,
    EXPECTED_EVENT_CLASSES,
    EventCoverageReport,
)


def _make_service_with_events(event_types: list[str]) -> AuditLogService:
    """Return an AuditLogService whose DB returns the given event_type rows."""
    mock_rows = [(et,) for et in event_types]

    mock_db = MagicMock()
    mock_query = MagicMock()
    mock_filter = MagicMock()
    mock_filter.all.return_value = mock_rows
    mock_query.filter.return_value = mock_filter
    mock_db.query.return_value = mock_query

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__enter__ = MagicMock(return_value=mock_db)
    mock_session_factory.return_value.__exit__ = MagicMock(return_value=False)

    return AuditLogService(db_session_factory=mock_session_factory)


class TestCheckEventCoverage(unittest.TestCase):
    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    def _coverage_by_class(self, report: EventCoverageReport) -> dict:
        return {c.event_class: c for c in report.coverage}

    # ------------------------------------------------------------------ #
    # Tests                                                                #
    # ------------------------------------------------------------------ #

    def test_all_classes_covered(self):
        """All expected classes are well-represented → all severities are 'ok'."""
        # Generate 10 events for every expected class
        events = []
        for prefix in EXPECTED_EVENT_CLASSES:
            events.extend([f"{prefix}event_{i}" for i in range(10)])

        service = _make_service_with_events(events)
        report = service.check_event_coverage(window_hours=24, underrepresented_threshold=5)

        self.assertIsInstance(report, EventCoverageReport)
        self.assertEqual(report.missing_classes, 0)
        self.assertEqual(report.underrepresented_classes, 0)
        self.assertEqual(report.covered_classes, len(EXPECTED_EVENT_CLASSES))

        by_class = self._coverage_by_class(report)
        for prefix in EXPECTED_EVENT_CLASSES:
            self.assertEqual(by_class[prefix].severity, "ok")
            self.assertTrue(by_class[prefix].present)
            self.assertEqual(by_class[prefix].event_count, 10)

    def test_empty_audit_stream_all_critical(self):
        """Empty audit log → every expected class is 'critical' (missing)."""
        service = _make_service_with_events([])
        report = service.check_event_coverage(window_hours=24, underrepresented_threshold=5)

        self.assertEqual(report.missing_classes, len(EXPECTED_EVENT_CLASSES))
        self.assertEqual(report.covered_classes, 0)
        self.assertEqual(report.underrepresented_classes, 0)

        by_class = self._coverage_by_class(report)
        for prefix in EXPECTED_EVENT_CLASSES:
            self.assertEqual(by_class[prefix].severity, "critical")
            self.assertFalse(by_class[prefix].present)
            self.assertEqual(by_class[prefix].event_count, 0)

    def test_partial_missing_classes(self):
        """Some classes absent → they are 'critical'; others are 'ok'."""
        # Only emit wallet and auth events, leave the rest missing
        events = [
            "wallet.created",
            "wallet.fetched",
            "wallet.fetched",
            "wallet.fetched",
            "wallet.fetched",
            "wallet.fetched",
            "auth.login_success",
            "auth.login_success",
            "auth.login_success",
            "auth.login_success",
            "auth.login_success",
        ]
        service = _make_service_with_events(events)
        report = service.check_event_coverage(window_hours=24, underrepresented_threshold=5)

        by_class = self._coverage_by_class(report)

        # Present classes
        self.assertEqual(by_class["wallet."].severity, "ok")
        self.assertEqual(by_class["auth."].severity, "ok")

        # Missing classes
        for prefix in ["job_", "sla.", "webhook.", "payment."]:
            self.assertEqual(by_class[prefix].severity, "critical", msg=prefix)

        self.assertEqual(report.missing_classes, 4)
        self.assertEqual(report.covered_classes, 2)

    def test_underrepresented_class(self):
        """Classes with count < threshold → 'warning', not 'critical'."""
        events = [
            # wallet: 2 events — below threshold of 5 → warning
            "wallet.created",
            "wallet.linked",
            # auth: 10 events — ok
            *[f"auth.login_success" for _ in range(10)],
            # job: 10 events — ok
            *[f"job_retried" for _ in range(10)],
            # sla: 10 events — ok
            *[f"sla.computed" for _ in range(10)],
            # webhook: 10 events — ok
            *[f"webhook.delivered" for _ in range(10)],
            # payment: 10 events — ok
            *[f"payment.sent" for _ in range(10)],
        ]
        service = _make_service_with_events(events)
        report = service.check_event_coverage(window_hours=24, underrepresented_threshold=5)

        by_class = self._coverage_by_class(report)
        self.assertEqual(by_class["wallet."].severity, "warning")
        self.assertEqual(by_class["wallet."].event_count, 2)
        self.assertTrue(by_class["wallet."].present)

        self.assertEqual(report.underrepresented_classes, 1)
        self.assertEqual(report.missing_classes, 0)

    def test_report_includes_impacted_metrics_and_ingestion_stage(self):
        """Coverage entries include impacted_metrics and ingestion_stage from taxonomy."""
        service = _make_service_with_events([])
        report = service.check_event_coverage()

        by_class = self._coverage_by_class(report)
        for prefix, meta in EXPECTED_EVENT_CLASSES.items():
            self.assertEqual(
                by_class[prefix].impacted_metrics,
                meta["impacted_metrics"],
            )
            self.assertEqual(
                by_class[prefix].ingestion_stage,
                meta["ingestion_stage"],
            )

    def test_to_dict_is_machine_readable(self):
        """EventCoverageReport.to_dict() produces a fully serialisable dict."""
        import json  # noqa: PLC0415

        service = _make_service_with_events(["wallet.created"] * 10)
        report = service.check_event_coverage()
        report_dict = report.to_dict()

        # Must be JSON-serialisable
        serialised = json.dumps(report_dict)
        self.assertIsInstance(serialised, str)

        parsed = json.loads(serialised)
        self.assertIn("checked_at", parsed)
        self.assertIn("coverage", parsed)
        self.assertIsInstance(parsed["coverage"], list)

    def test_window_hours_respected(self):
        """window_hours parameter is forwarded to the DB filter correctly."""
        mock_rows = [("wallet.created",) for _ in range(10)]
        mock_db = MagicMock()
        mock_filter = MagicMock()
        mock_filter.all.return_value = mock_rows
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_filter
        mock_db.query.return_value = mock_query

        factory = MagicMock()
        factory.return_value.__enter__ = MagicMock(return_value=mock_db)
        factory.return_value.__exit__ = MagicMock(return_value=False)

        service = AuditLogService(db_session_factory=factory)
        report = service.check_event_coverage(window_hours=6)

        self.assertEqual(report.window_hours, 6)


if __name__ == "__main__":
    unittest.main()
