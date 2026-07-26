"""Tests for BE-021, BE-027, BE-034, BE-042 implementations.

Covers:
- BE-021: SLA result uniqueness and locking
- BE-027: Payment reconciliation history
- BE-034: Webhook secret rotation auditing
- BE-042: Job retention and cleanup
"""
import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models.job import Job, JobStatus, JobType
from app.models.orm.audit_log import AuditLogORM
from app.models.orm.sla import SLAResultORM
from app.models.webhook import Webhook, WebhookEvent
from app.repositories.sla_repository import SLARepository
from app.repositories.payment_repository import PaymentRepository
from app.services.job_cleanup import JobCleanupService


# ============================================================================
# BE-021: SLA Result Uniqueness and Locking Tests
# ============================================================================

class TestBE021SlaUniqueness:
    """Test database-level uniqueness and locking for SLA results."""

    def test_create_sla_result_sets_latest_flag(self, db_session: Session):
        """Creating a new SLA result should set is_latest=True."""
        repo = SLARepository(db_session)
        
        sla_data = {
            "outage_id": "test-outage-001",
            "status": "met",
            "mttr_minutes": 30,
            "threshold_minutes": 60,
            "amount": 100.0,
            "payment_type": "reward",
            "rating": "excellent",
        }
        
        result = repo.create(sla_data)
        
        assert result.outage_id == "test-outage-001"
        assert result.status == "met"
        
        # Verify is_latest is True in database
        orm = db_session.query(SLAResultORM).filter(
            SLAResultORM.outage_id == "test-outage-001"
        ).first()
        assert orm.is_latest is True

    def test_create_new_result_demotes_previous_latest(self, db_session: Session):
        """Creating a new SLA result should demote the previous latest."""
        repo = SLARepository(db_session)
        
        sla_data_1 = {
            "outage_id": "test-outage-002",
            "status": "met",
            "mttr_minutes": 30,
            "threshold_minutes": 60,
            "amount": 100.0,
            "payment_type": "reward",
            "rating": "excellent",
        }
        
        result_1 = repo.create(sla_data_1)
        
        # Create second result for same outage
        sla_data_2 = sla_data_1.copy()
        sla_data_2["mttr_minutes"] = 45
        result_2 = repo.create(sla_data_2)
        
        # Verify only one is_latest=True per outage
        latest_results = db_session.query(SLAResultORM).filter(
            SLAResultORM.outage_id == "test-outage-002",
            SLAResultORM.is_latest.is_(True)
        ).all()
        
        assert len(latest_results) == 1
        assert latest_results[0].id == result_2.id

    def test_get_by_outage_returns_latest(self, db_session: Session):
        """get_by_outage should return the result with is_latest=True."""
        repo = SLARepository(db_session)
        
        # Create multiple results
        sla_data_1 = {
            "outage_id": "test-outage-003",
            "status": "met",
            "mttr_minutes": 30,
            "threshold_minutes": 60,
            "amount": 100.0,
            "payment_type": "reward",
            "rating": "excellent",
        }
        
        repo.create(sla_data_1)
        
        sla_data_2 = sla_data_1.copy()
        sla_data_2["status"] = "violated"
        sla_data_2["mttr_minutes"] = 90
        repo.create(sla_data_2)
        
        # Should return the latest one
        latest = repo.get_by_outage("test-outage-003")
        
        assert latest is not None
        assert latest.status == "violated"
        assert latest.mttr_minutes == 90


# ============================================================================
# BE-027: Payment Reconciliation History Tests
# ============================================================================

class TestBE027PaymentReconciliationHistory:
    """Test payment reconciliation history endpoint."""

    def test_reconciliation_history_empty(self, db_session: Session):
        """New payment should have empty reconciliation history."""
        from app.models.payment import PaymentTransaction
        from app.models.orm.payment import PaymentTransactionORM
        
        # Create a test payment
        payment_orm = PaymentTransactionORM(
            id="test-pay-001",
            transaction_hash="test-hash-001",
            type="reward",
            amount=100.0,
            asset_code="XLM",
            from_address="from_addr",
            to_address="to_addr",
            status="pending",
        )
        db_session.add(payment_orm)
        db_session.commit()
        
        repo = PaymentRepository(db_session)
        history = repo.get_reconciliation_history("test-pay-001")
        
        assert history == []

    def test_reconciliation_history_with_events(self, db_session: Session):
        """Reconciliation history should show status transitions."""
        from app.models.orm.payment import PaymentTransactionORM
        
        # Create a test payment
        payment_orm = PaymentTransactionORM(
            id="test-pay-002",
            transaction_hash="test-hash-002",
            type="reward",
            amount=100.0,
            asset_code="XLM",
            from_address="from_addr",
            to_address="to_addr",
            status="confirmed",
        )
        db_session.add(payment_orm)
        db_session.commit()
        
        # Add audit log entries
        audit_1 = AuditLogORM(
            event_type="payment_reconciled",
            email="admin@example.com",
            details={
                "id": "test-pay-002",
                "previous_status": "pending",
                "status": "confirmed",
            },
            created_at=datetime.utcnow(),
        )
        db_session.add(audit_1)
        db_session.commit()
        
        repo = PaymentRepository(db_session)
        history = repo.get_reconciliation_history("test-pay-002")
        
        assert len(history) == 1
        assert history[0]["event_type"] == "payment_reconciled"
        assert history[0]["actor"] == "admin@example.com"
        assert history[0]["previous_status"] == "pending"
        assert history[0]["new_status"] == "confirmed"
        assert history[0]["timestamp"] is not None


# ============================================================================
# BE-034: Webhook Secret Rotation Auditing Tests
# ============================================================================

class TestBE034WebhookSecretRotation:
    """Test webhook secret rotation auditing and metadata."""

    def test_webhook_has_secret_metadata_fields(self, db_session: Session):
        """Webhook model should have secret_version and last_secret_rotation_at."""
        webhook = Webhook(
            name="test-webhook",
            url="https://example.com/webhook",
            secret="initial-secret",
            events=json.dumps(["sla.violation"]),
        )
        db_session.add(webhook)
        db_session.commit()
        db_session.refresh(webhook)
        
        assert webhook.secret_version == 1
        assert webhook.last_secret_rotation_at is None

    def test_secret_rotation_increments_version(self, db_session: Session):
        """Rotating secret should increment version and set timestamp."""
        from datetime import datetime
        
        webhook = Webhook(
            name="test-webhook-rotate",
            url="https://example.com/webhook",
            secret="initial-secret",
            events=json.dumps(["sla.violation"]),
        )
        db_session.add(webhook)
        db_session.commit()
        db_session.refresh(webhook)
        
        initial_version = webhook.secret_version
        
        # Simulate rotation
        webhook.secret = "new-secret-value"
        webhook.secret_version = initial_version + 1
        webhook.last_secret_rotation_at = datetime.utcnow()
        db_session.commit()
        db_session.refresh(webhook)
        
        assert webhook.secret_version == 2
        assert webhook.last_secret_rotation_at is not None
        assert isinstance(webhook.last_secret_rotation_at, datetime)


# ============================================================================
# BE-042: Job Retention and Cleanup Tests
# ============================================================================

class TestBE042JobRetentionCleanup:
    """Test job retention and cleanup policy."""

    def test_cleanup_dry_run_does_not_delete(self, db_session: Session):
        """Dry run cleanup should count but not delete jobs."""
        # Create old successful jobs
        old_date = datetime.utcnow() - timedelta(days=60)
        
        for i in range(5):
            job = Job(
                celery_task_id=f"task-dry-{i}",
                job_type=JobType.SLA_COMPUTATION,
                status=JobStatus.SUCCESS,
                finished_at=old_date,
                created_at=old_date,
            )
            db_session.add(job)
        
        db_session.commit()
        
        cleanup_service = JobCleanupService(db_session)
        result = cleanup_service.cleanup_old_jobs(dry_run=True)
        
        assert result["total_deleted"] == 0
        assert result["successful_jobs_deleted"] == 5
        assert result["dry_run"] is True
        
        # Verify jobs still exist
        count = db_session.query(Job).filter(
            Job.celery_task_id.like("task-dry-%")
        ).count()
        assert count == 5

    def test_cleanup_deletes_old_successful_jobs(self, db_session: Session):
        """Cleanup should delete successful jobs older than retention period."""
        old_date = datetime.utcnow() - timedelta(days=60)
        
        for i in range(3):
            job = Job(
                celery_task_id=f"task-old-success-{i}",
                job_type=JobType.SLA_COMPUTATION,
                status=JobStatus.SUCCESS,
                finished_at=old_date,
                created_at=old_date,
            )
            db_session.add(job)
        
        db_session.commit()
        
        cleanup_service = JobCleanupService(db_session)
        result = cleanup_service.cleanup_old_jobs(
            successful_retention_days=30,
            dry_run=False,
        )
        
        assert result["successful_jobs_deleted"] == 3
        assert result["total_deleted"] == 3
        
        # Verify jobs are deleted
        count = db_session.query(Job).filter(
            Job.celery_task_id.like("task-old-success-%")
        ).count()
        assert count == 0

    def test_cleanup_preserves_recent_failed_jobs(self, db_session: Session):
        """Cleanup should preserve failed jobs within retention period."""
        recent_date = datetime.utcnow() - timedelta(days=10)
        
        job = Job(
            celery_task_id="task-recent-failed",
            job_type=JobType.SLA_COMPUTATION,
            status=JobStatus.FAILURE,
            finished_at=recent_date,
            created_at=recent_date,
        )
        db_session.add(job)
        db_session.commit()
        
        cleanup_service = JobCleanupService(db_session)
        result = cleanup_service.cleanup_old_jobs(
            failed_retention_days=90,
            dry_run=False,
        )
        
        assert result["failed_jobs_deleted"] == 0
        
        # Verify job still exists
        count = db_session.query(Job).filter(
            Job.celery_task_id == "task-recent-failed"
        ).count()
        assert count == 1

    def test_cleanup_deletes_old_failed_jobs(self, db_session: Session):
        """Cleanup should delete failed jobs older than retention period."""
        old_date = datetime.utcnow() - timedelta(days=120)
        
        job = Job(
            celery_task_id="task-old-failed",
            job_type=JobType.SLA_COMPUTATION,
            status=JobStatus.FAILURE,
            finished_at=old_date,
            created_at=old_date,
        )
        db_session.add(job)
        db_session.commit()
        
        cleanup_service = JobCleanupService(db_session)
        result = cleanup_service.cleanup_old_jobs(
            failed_retention_days=90,
            dry_run=False,
        )
        
        assert result["failed_jobs_deleted"] == 1
        
        # Verify job is deleted
        count = db_session.query(Job).filter(
            Job.celery_task_id == "task-old-failed"
        ).count()
        assert count == 0

    def test_retention_stats_returns_correct_counts(self, db_session: Session):
        """Retention stats should accurately count jobs by status and age."""
        now = datetime.utcnow()
        old_date = now - timedelta(days=45)
        
        # Create jobs with different statuses and ages
        for i in range(3):
            db_session.add(Job(
                celery_task_id=f"task-recent-success-{i}",
                job_type=JobType.SLA_COMPUTATION,
                status=JobStatus.SUCCESS,
                finished_at=now,
                created_at=now,
            ))
        
        for i in range(2):
            db_session.add(Job(
                celery_task_id=f"task-old-success-{i}",
                job_type=JobType.SLA_COMPUTATION,
                status=JobStatus.SUCCESS,
                finished_at=old_date,
                created_at=old_date,
            ))
        
        db_session.commit()
        
        cleanup_service = JobCleanupService(db_session)
        stats = cleanup_service.get_retention_stats()
        
        assert stats["total_jobs"] == 5
        assert stats["by_status"]["success"] == 5
        assert stats["by_age"]["older_than_30_days"] == 2


# Pytest fixtures
@pytest.fixture
def db_session():
    """Create a test database session."""
    # This fixture should be provided by your test infrastructure
    # Using conftest.py or similar
    pass
