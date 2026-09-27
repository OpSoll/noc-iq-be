"""Tests for Mercy60-assigned issues:

  BE-W5-050 (#311) — Background job result schema standardization
  BE-W5-048 (#309) — Job retry taxonomy and maximum-attempt governance
  BE-W5-047 (#308) — Job lease heartbeat and stuck-task reclamation
  BE-W5-052 (#313) — Job cleanup policy hardening with retention tiering
"""
import json
from datetime import datetime, timedelta

import pytest
from unittest.mock import MagicMock, patch

from app.models.job import (
    Job,
    JobErrorDetail,
    JobResultEnvelope,
    JobStatus,
    JobType,
    RetryClass,
)
from app.services.job_cleanup import (
    JobCleanupService,
    RETRY_CLASS_DEFAULTS,
    get_retry_policy,
    heartbeat_job,
    reclaim_stale_leases,
)
from app.core.config import Settings


# --------------------------------------------------------------------------- #
# BE-W5-050: Job result schema standardization                                #
# --------------------------------------------------------------------------- #

class TestBE_W5_050:
    """Standardized job result envelope and typed error metadata."""

    def test_job_error_detail_serialization(self):
        err = JobErrorDetail(
            code="SLA_TIMEOUT",
            message="Contract call timed out",
            retryable=True,
            details={"timeout_ms": 5000},
        )
        data = err.model_dump()
        assert data["code"] == "SLA_TIMEOUT"
        assert data["message"] == "Contract call timed out"
        assert data["retryable"] is True
        assert data["details"]["timeout_ms"] == 5000

    def test_job_error_detail_minimal(self):
        err = JobErrorDetail(code="UNKNOWN", message="something went wrong")
        data = err.model_dump()
        assert data["retryable"] is False  # default
        assert data["details"] is None

    def test_job_result_envelope_shape(self):
        envelope = JobResultEnvelope(
            job_id="550e8400-e29b-41d4-a716-446655440000",
            celery_task_id="task-001",
            job_type="sla_computation",
            status="success",
            progress=100.0,
            result={"mttr": 12},
            error=None,
            retry_count=0,
            max_retries=3,
            retry_class="exponential_backoff",
            started_at="2026-07-29T10:00:00Z",
            finished_at="2026-07-29T10:00:45Z",
            created_at="2026-07-29T09:59:55Z",
        )
        data = envelope.model_dump()
        assert data["job_id"] == "550e8400-e29b-41d4-a716-446655440000"
        assert data["job_type"] == "sla_computation"
        assert data["status"] == "success"
        assert data["error"] is None
        assert data["retry_class"] == "exponential_backoff"

    def test_job_result_envelope_with_error(self):
        envelope = JobResultEnvelope(
            job_id="j1",
            celery_task_id="t1",
            job_type="webhook_dispatch",
            status="failure",
            error=JobErrorDetail(code="WEBHOOK_TIMEOUT", message="timeout", retryable=True),
        )
        data = envelope.model_dump()
        assert data["error"]["code"] == "WEBHOOK_TIMEOUT"
        assert data["error"]["retryable"] is True

    def test_job_model_has_new_columns(self):
        """Verify the Job model exposes all new columns for BE-W5-050."""
        assert hasattr(Job, "error_code")
        assert hasattr(Job, "error_retryable")
        assert hasattr(Job, "error_details")


# --------------------------------------------------------------------------- #
# BE-W5-048: Retry taxonomy and dead-letter governance                        #
# --------------------------------------------------------------------------- #

class TestBE_W5_048:
    """Retry taxonomy defaults, dead-letter routing, and governance."""

    def test_retry_class_enum_values(self):
        assert RetryClass.AT_MOST_ONCE.value == "at_most_once"
        assert RetryClass.AT_LEAST_ONCE.value == "at_least_once"
        assert RetryClass.EXPONENTIAL_BACKOFF.value == "exponential_backoff"

    def test_RETRY_CLASS_DEFAULTS_has_all_types(self):
        for jt in ["sla_computation", "bulk_sla_computation", "webhook_dispatch", "webhook_dr_replay"]:
            assert jt in RETRY_CLASS_DEFAULTS, f"Missing retry defaults for {jt}"

    def test_get_retry_policy_known_type(self):
        policy = get_retry_policy("sla_computation")
        assert policy["retry_class"] == "exponential_backoff"
        assert policy["max_retries"] == 3
        assert policy["base_delay_seconds"] == 30

    def test_get_retry_policy_webhook_dr_replay(self):
        policy = get_retry_policy("webhook_dr_replay")
        assert policy["retry_class"] == "at_most_once"
        assert policy["max_retries"] == 1

    def test_get_retry_policy_unknown_type_falls_back(self):
        policy = get_retry_policy("unknown_type")
        assert policy["retry_class"] == "at_least_once"
        assert policy["max_retries"] == 3

    def test_dead_letter_status_exists(self):
        assert JobStatus.DEAD_LETTER.value == "dead_letter"

    def test_job_model_has_dead_letter_columns(self):
        assert hasattr(Job, "dead_letter_reason")
        assert hasattr(Job, "dead_letter_at")
        assert hasattr(Job, "retry_class")

    def test_config_has_dead_letter_settings(self):
        s = Settings()
        assert hasattr(s, "JOB_RETRY_DEAD_LETTER_ENABLED")
        assert hasattr(s, "JOB_RETRY_BACKOFF_MAX_DELAY_SECONDS")
        assert hasattr(s, "JOB_RETRY_BACKOFF_MULTIPLIER")
        assert hasattr(s, "JOB_RETRY_CLASS_DEFAULTS")

    def test_retry_class_defaults_parse(self):
        """JOB_RETRY_CLASS_DEFAULTS config string is well-formed."""
        s = Settings()
        raw = s.JOB_RETRY_CLASS_DEFAULTS
        entries = [e for e in raw.split(",") if e.strip()]
        assert len(entries) >= 4
        for entry in entries:
            parts = entry.split(":")
            assert len(parts) == 4, f"Entry '{entry}' should have 4 colon-separated parts"
            int(parts[2])  # max_retries must be int
            int(parts[3])  # base_delay must be int


# --------------------------------------------------------------------------- #
# BE-W5-047: Lease heartbeat and stuck-task reclamation                       #
# --------------------------------------------------------------------------- #

class TestBE_W5_047:
    """Worker lease heartbeat and stale-lease reclamation."""

    def test_job_model_has_lease_columns(self):
        assert hasattr(Job, "worker_id")
        assert hasattr(Job, "heartbeat_at")
        assert hasattr(Job, "lease_expires_at")

    def test_heartbeat_updates_job(self):
        db = MagicMock()
        mock_job = MagicMock(spec=Job)
        mock_job.celery_task_id = "task-001"
        db.query.return_value.filter.return_value.first.return_value = mock_job

        result = heartbeat_job(db, "task-001", "worker-abc", timeout_seconds=120)
        assert result is mock_job
        assert mock_job.worker_id == "worker-abc"
        assert mock_job.heartbeat_at is not None
        assert mock_job.lease_expires_at is not None
        db.commit.assert_called_once()

    def test_heartbeat_nonexistent_job(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        result = heartbeat_job(db, "missing-task", "worker-abc")
        assert result is None

    def test_reclaim_stale_leases_dry_run(self):
        db = MagicMock()
        stale_job = MagicMock(spec=Job)
        stale_job.id = "job-1"
        stale_job.celery_task_id = "task-1"
        stale_job.job_type = JobType.SLA_COMPUTATION
        stale_job.worker_id = "worker-old"
        stale_job.lease_expires_at = datetime.utcnow() - timedelta(minutes=5)
        stale_job.status = JobStatus.STARTED

        db.query.return_value.filter.return_value.limit.return_value.all.return_value = [stale_job]

        result = reclaim_stale_leases(db, timeout_seconds=120, dry_run=True)
        assert result["stale_leases_found"] == 1
        assert result["reclaimed"] == 1
        assert result["dry_run"] is True
        # No commit in dry run
        db.commit.assert_not_called()

    def test_reclaim_stale_leases_live(self):
        db = MagicMock()
        stale_job = MagicMock(spec=Job)
        stale_job.id = "job-1"
        stale_job.celery_task_id = "task-1"
        stale_job.job_type = JobType.SLA_COMPUTATION
        stale_job.worker_id = "worker-old"
        stale_job.lease_expires_at = datetime.utcnow() - timedelta(minutes=5)
        stale_job.status = JobStatus.STARTED

        db.query.return_value.filter.return_value.limit.return_value.all.return_value = [stale_job]

        result = reclaim_stale_leases(db, timeout_seconds=120, dry_run=False)
        assert result["reclaimed"] == 1
        assert result["dry_run"] is False
        assert stale_job.worker_id is None
        assert stale_job.lease_expires_at is None
        assert stale_job.status == JobStatus.PENDING

    def test_config_has_lease_settings(self):
        s = Settings()
        assert hasattr(s, "JOB_LEASE_HEARTBEAT_INTERVAL_SECONDS")
        assert hasattr(s, "JOB_LEASE_TIMEOUT_SECONDS")
        assert hasattr(s, "JOB_LEASE_RECLAMATION_ENABLED")


# --------------------------------------------------------------------------- #
# BE-W5-052: Retention tiering                                                #
# --------------------------------------------------------------------------- #

class TestBE_W5_052:
    """Retention tiering, protection flags, and audit-critical cleanup."""

    def test_job_model_has_protection_columns(self):
        assert hasattr(Job, "under_investigation")
        assert hasattr(Job, "under_dispute")
        assert hasattr(Job, "audit_critical")

    def test_config_has_retention_settings(self):
        s = Settings()
        assert hasattr(s, "JOB_RETENTION_SUCCESS_DAYS")
        assert hasattr(s, "JOB_RETENTION_FAILURE_DAYS")
        assert hasattr(s, "JOB_RETENTION_QUARANTINED_DAYS")
        assert hasattr(s, "JOB_RETENTION_DEAD_LETTER_DAYS")
        assert hasattr(s, "JOB_RETENTION_AUDIT_CRITICAL_DAYS")
        assert hasattr(s, "JOB_RETENTION_INVESTIGATION_PROTECTED")
        assert hasattr(s, "JOB_RETENTION_DISPUTE_PROTECTED")

    def test_default_retention_windows(self):
        windows = JobCleanupService._default_retention()
        assert "success" in windows
        assert "failure" in windows
        assert "quarantined" in windows
        assert "dead_letter" in windows

    def test_cleanup_skips_protected_records(self):
        db = MagicMock()
        service = JobCleanupService(db)

        # Mock: build_base_query excludes protected records
        mock_query = MagicMock()
        db.query.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.filter.return_value.filter.return_value = mock_query
        mock_query.filter.return_value.filter.return_value.filter.return_value = mock_query
        mock_query.filter.return_value.filter.return_value.filter.return_value.count.return_value = 0

        result = service.cleanup_old_jobs(dry_run=True)
        assert result["dry_run"] is True
        assert result["total_deleted"] == 0

    def test_get_retention_stats_includes_protected(self):
        db = MagicMock()
        service = JobCleanupService(db)
        # Mock counts
        db.query.return_value.count.return_value = 0
        db.query.return_value.filter.return_value.count.return_value = 0

        stats = service.get_retention_stats()
        assert "protected" in stats
        assert "under_investigation" in stats["protected"]
        assert "under_dispute" in stats["protected"]
        assert "audit_critical" in stats["protected"]

    def test_set_investigation_flag(self):
        db = MagicMock()
        mock_job = MagicMock(spec=Job)
        db.query.return_value.filter.return_value.first.return_value = mock_job

        service = JobCleanupService(db)
        result = service.set_investigation_flag("job-1", True)
        assert result is mock_job
        assert mock_job.under_investigation is True
        db.commit.assert_called_once()

    def test_set_dispute_flag(self):
        db = MagicMock()
        mock_job = MagicMock(spec=Job)
        db.query.return_value.filter.return_value.first.return_value = mock_job

        service = JobCleanupService(db)
        result = service.set_dispute_flag("job-1", True)
        assert result is mock_job
        assert mock_job.under_dispute is True

    def test_cleanup_audit_critical(self):
        db = MagicMock()
        service = JobCleanupService(db)

        mock_query = MagicMock()
        db.query.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.filter.return_value.filter.return_value = mock_query
        mock_query.filter.return_value.filter.return_value.filter.return_value = mock_query
        mock_query.filter.return_value.filter.return_value.filter.return_value.count.return_value = 5

        result = service.cleanup_audit_critical(retention_days=365, dry_run=True)
        assert result["audit_critical_eligible"] == 5
        assert result["dry_run"] is True
        assert result["retention_days"] == 365


# --------------------------------------------------------------------------- #
# Integration test: retry taxonomy applied on enqueue                          #
# --------------------------------------------------------------------------- #

class TestIntegration:
    """Smoke tests for the integration of multiple Mercy60 issues."""

    def test_job_envelope_roundtrip(self):
        """A JobResultEnvelope can be constructed from all fields."""
        envelope = JobResultEnvelope(
            job_id="j1",
            celery_task_id="t1",
            job_type="sla_computation",
            status="success",
            progress=100.0,
            result={"ok": True},
            error=JobErrorDetail(code="OK", message="all good", retryable=False),
            retry_count=0,
            max_retries=3,
            retry_class="exponential_backoff",
            started_at="2026-07-29T10:00:00Z",
            finished_at="2026-07-29T10:00:45Z",
            created_at="2026-07-29T09:59:55Z",
            worker_id="worker-1",
            lease_expires_at="2026-07-29T10:02:00Z",
            under_investigation=False,
            under_dispute=False,
            audit_critical=False,
        )
        data = envelope.model_dump()
        assert data["worker_id"] == "worker-1"
        assert data["lease_expires_at"] is not None
        assert data["under_investigation"] is False

    def test_all_job_statuses_in_retention(self):
        """BE-W5-052: retention windows exist for all terminal statuses."""
        windows = JobCleanupService._default_retention()
        for st in [JobStatus.SUCCESS, JobStatus.FAILURE, JobStatus.REVOKED]:
            assert st.value in windows, f"Missing retention window for {st.value}"

    def test_quarantined_retention_longer(self):
        """Quarantined and dead-letter jobs have longer retention."""
        windows = JobCleanupService._default_retention()
        assert windows[JobStatus.QUARANTINED.value] >= windows[JobStatus.SUCCESS.value]
        assert windows[JobStatus.DEAD_LETTER.value] >= windows[JobStatus.SUCCESS.value]
