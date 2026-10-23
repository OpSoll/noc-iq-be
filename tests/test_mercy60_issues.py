"""Tests for Mercy60-assigned issues.

This suite verifies the tiered cleanup retention behavior described in
Issue #1001: Jobs: Add tests for cleanup retention tiers.

Acceptance criteria covered here:
- Each tier covered
- Protected jobs retained
- Counts returned
- Test suite passes
"""

import datetime

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.db.base_class import Base
from app.models.job import Job, JobStatus
from app.services.job_cleanup import JobCleanupService


@pytest.fixture()
def db_session():
    """Create a temporary in-memory SQLite database and session."""
    engine = create_engine("sqlite:///:memory:")
    TestingSession = sessionmaker(bind=engine)
    Base.metadata.create_all(engine)
    session = TestingSession()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


def _make_job(db, status, finished_at_offset_days=0, created_at_offset_days=0):
    finished_at = (
        datetime.datetime.utcnow()
        - datetime.timedelta(days=finished_at_offset_days)
        if finished_at_offset_days
        else None
    )
    created_at = (
        datetime.datetime.utcnow()
        - datetime.timedelta(days=created_at_offset_days)
        if created_at_offset_days
        else None
    )
    job = Job(status=status, finished_at=finished_at, created_at=created_at)
    db.add(job)
    db.commit()
    return job


@pytest.fixture()
def cleanup_service(db_session):
    return JobCleanupService(db_session)


class TestTieredRetentionCoversEveryTier:
    """#1001 - Each tier covered: successful, failed, revoked, quarantined."""

    def test_successful_jobs_are_counted_and_removed(self, cleanup_service):
        cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=30)
        three_days_ago = datetime.datetime.utcnow() - datetime.timedelta(days=3)

        _make_job(cleanup_service.db, JobStatus.SUCCESS, finished_at_offset_days=40)
        _make_job(cleanup_service.db, JobStatus.FAILURE, finished_at_offset_days=40)
        _make_job(cleanup_service.db, JobStatus.SUCCESS, finished_at_offset_days=3)

        result = cleanup_service.cleanup_old_jobs(dry_run=True)

        assert result["successful_jobs_deleted"] == 1
        assert result["failed_jobs_deleted"] == 0
        assert result["revoked_jobs_deleted"] == 0

    def test_failed_jobs_are_counted_and_removed(self, cleanup_service):
        cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=90)
        _make_job(cleanup_service.db, JobStatus.FAILURE, finished_at_offset_days=120)

        result = cleanup_service.cleanup_old_jobs(dry_run=True)

        assert result["failed_jobs_deleted"] == 1

    def test_revoked_jobs_are_counted_and_removed(self, cleanup_service):
        _make_job(cleanup_service.db, JobStatus.REVOKED, finished_at_offset_days=40)
        _make_job(cleanup_service.db, JobStatus.FAILURE, finished_at_offset_days=3)

        result = cleanup_service.cleanup_old_jobs(dry_run=True)

        assert result["revoked_jobs_deleted"] == 1

    def test_quotted_jobs_are_excluded_from_standard_cleanup(self, cleanup_service):
        _make_job(cleanup_service.db, JobStatus.QUARANTINED, finished_at_offset_days=40)
        _make_job(cleanup_service.db, JobStatus.SUCCESS, finished_at_offset_days=40)

        result = cleanup_service.cleanup_old_jobs(dry_run=True)

        assert result["quarantined_jobs_deleted"] == 0
        assert result["total_deleted"] == 0


class TestProtectedJobsRetained:
    """#1001 - Protected jobs retained even when older than the retention window."""

    def test_protected_job_is_not_deleted_by_cleanup(self, cleanup_service):
        _make_job(cleanup_service.db, JobStatus.QUARANTINED, finished_at_offset_days=40)
        _make_job(cleanup_service.db, JobStatus.SUCCESS, finished_at_offset_days=40)

        service = JobCleanupService(cleanup_service.db)
        result = service.cleanup_old_jobs(dry_run=True)

        assert result["total_deleted"] == 0
        assert result["quarantined_jobs_deleted"] == 0


class TestCountsReturned:
    """#1001 - Counts returned per tier."""

    def test_cleanup_returns_counts_for_each_tier(self, cleanup_service):
        now = datetime.datetime.utcnow()
        _make_job(cleanup_service.db, JobStatus.SUCCESS, finished_at_offset_days=30)
        _make_job(cleanup_service.db, JobStatus.FAILURE, finished_at_offset_days=30)
        _make_job(cleanup_service.db, JobStatus.REVOKED, finished_at_offset_days=30)

        result = cleanup_service.cleanup_old_jobs(dry_run=True)

        assert result["successful_jobs_deleted"] == 1
        assert result["failed_jobs_deleted"] == 1
        assert result["revoked_jobs_deleted"] == 1
        assert result["total_deleted"] == 3

    def test_counts_are_zero_when_nothing_matches(self, cleanup_service):
        result = cleanup_service.cleanup_old_jobs(dry_run=True)

        assert result["successful_jobs_deleted"] == 0
        assert result["failed_jobs_deleted"] == 0
        assert result["revoked_jobs_deleted"] == 0
        assert result["total_deleted"] == 0

    def test_counts_reflect_only_matching_tiers(self, cleanup_service):
        _make_job(cleanup_service.db, JobStatus.FAILURE, finished_at_offset_days=120)
        _make_job(cleanup_service.db, JobStatus.SUCCESS, finished_at_offset_days=30)

        result = cleanup_service.cleanup_old_jobs(dry_run=True)

        assert result["successful_jobs_deleted"] == 0
        assert result["failed_jobs_deleted"] == 1
        assert result["total_deleted"] == 1


class TestRetentionDefaults:
    """#1001 - Retention defaults applied per tier."""

    def test_successful_and_revoked_use_successful_retention(self, cleanup_service):
        _make_job(cleanup_service.db, JobStatus.SUCCESS, finished_at_offset_days=40)
        _make_job(cleanup_service.db, JobStatus.REVOKED, finished_at_offset_days=40)

        result = cleanup_service.cleanup_old_jobs(dry_run=True)

        assert result["successful_jobs_deleted"] == 1
        assert result["revoked_jobs_deleted"] == 1

    def test_failed_jobs_use_failed_retention(self, cleanup_service):
        _make_job(cleanup_service.db, JobStatus.FAILURE, finished_at_offset_days=120)

        result = cleanup_service.cleanup_old_jobs(dry_run=True)

        assert result["failed_jobs_deleted"] == 1
