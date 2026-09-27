"""
Periodic Celery task: audit-backed analytics quality checks.

Issue #374 (BE-W5-113): Detects analytics blind spots by cross-checking
expected event classes against the audit log stream, publishing operational
metrics and producing a structured JSON quality report.
"""
import json
import logging
from datetime import datetime, timezone

from app.tasks.celery_app import celery_app
from app.services.audit_log import audit_log
from app.services.metrics import metrics

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Thresholds (events per 24h window)
# ---------------------------------------------------------------------------
# Classes with fewer than this many events are flagged as underrepresented.
UNDERREPRESENTED_THRESHOLD = 5


@celery_app.task(
    name="app.tasks.analytics_quality_tasks.run_analytics_quality_checks",
    bind=False,
)
def run_analytics_quality_checks(window_hours: int = 24) -> dict:
    """
    Scheduled task: cross-check expected event classes against the audit stream.

    Runs every 6 hours via Celery beat.  Publishes Prometheus-style gauges:
      - analytics_quality.missing_event_classes   (count)
      - analytics_quality.underrepresented_classes (count)
      - analytics_quality.covered_classes          (count)

    Returns:
        Structured quality report dict (machine-readable JSON).
    """
    logger.info(
        "Running analytics quality checks (window_hours=%d)", window_hours
    )

    report = audit_log.check_event_coverage(
        window_hours=window_hours,
        underrepresented_threshold=UNDERREPRESENTED_THRESHOLD,
    )

    # ------------------------------------------------------------------ #
    # Publish operational metrics                                         #
    # ------------------------------------------------------------------ #
    metrics.set_gauge(
        "analytics_quality.missing_event_classes",
        float(report.missing_classes),
    )
    metrics.set_gauge(
        "analytics_quality.underrepresented_classes",
        float(report.underrepresented_classes),
    )
    metrics.set_gauge(
        "analytics_quality.covered_classes",
        float(report.covered_classes),
    )

    report_dict = report.to_dict()

    if report.missing_classes > 0:
        logger.warning(
            "Analytics quality check: %d missing event class(es): %s",
            report.missing_classes,
            [c["event_class"] for c in report_dict["coverage"] if c["severity"] == "critical"],
        )
    if report.underrepresented_classes > 0:
        logger.warning(
            "Analytics quality check: %d underrepresented event class(es): %s",
            report.underrepresented_classes,
            [c["event_class"] for c in report_dict["coverage"] if c["severity"] == "warning"],
        )

    logger.info(
        "Analytics quality check complete: covered=%d missing=%d underrepresented=%d",
        report.covered_classes,
        report.missing_classes,
        report.underrepresented_classes,
    )

    return report_dict
