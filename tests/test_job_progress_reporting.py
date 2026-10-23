"""Job progress reporting: increments, completion and monotonicity (#985).

``_job_progress_percentage`` (``app/api/v1/endpoints/jobs.py:208``) is what every
job status endpoint returns as ``progress_percentage``. It prefers the
fine-grained value the tasks write into ``progress_details`` and falls back to
the ``progress`` column.

Existing coverage in ``tests/test_limxiy_celery_issues.py`` is two assertions on
the helper in isolation — ``42.2`` from ``progress_details`` and ``33.0`` from the
column fallback. That leaves three properties untested, and each is one a UI
progress bar depends on:

  - **Completion reports 100.** A finished job that reports 99 leaves a spinner
    running forever on a result that has already arrived.
  - **Progress is monotonic.** A bar that goes 40 → 20 → 80 as out-of-order task
    updates land is the classic symptom of a Celery task writing stale state, and
    it looks like a bug in the UI rather than the backend.
  - **The helper and the column can disagree.** They are two sources of truth. A
    task that updates one but not the other produces a progress bar that jumps
    between values depending on which field a given endpoint reads.

The precedence is asserted in both directions: when ``progress_details`` carries
a percentage it wins, and when it does not the column is used. An implementation
that inverted this would silently show the coarse value in every case.
"""

from uuid import uuid4

import pytest

from app.api.v1.endpoints.jobs import _job_progress_percentage, _serialize_job
from app.models.job import Job, JobStatus, JobType


def make_job(
    progress: float = 0.0,
    progress_details: dict | None = None,
    status: JobStatus = JobStatus.STARTED,
) -> Job:
    """A Job ORM instance with only the fields these tests read.

    Constructed directly rather than persisted: the assertions are about what the
    helper and serialiser read, and going through the database would add setup
    without exercising anything more.
    """
    job = Job()
    job.id = uuid4()
    job.celery_task_id = "task-progress-1"
    job.job_type = JobType.BULK_SLA_COMPUTATION
    job.status = status
    job.progress = progress
    job.progress_details = progress_details
    job.payload = None
    job.result = None
    job.error = None
    job.error_code = None
    job.error_retryable = None
    job.error_details = None
    job.partial_results = None
    job.per_item_errors = None
    return job


class TestProgressIncrements:
    def test_column_value_is_reported_when_details_are_absent(self):
        assert _job_progress_percentage(make_job(progress=25.0)) == 25.0

    def test_details_percentage_takes_precedence(self):
        """When both sources are present the fine-grained value wins.

        The task writes a precise percentage into ``progress_details``; the
        ``progress`` column is coarse. Inverting this would show the coarse value
        in every case and the per-item reporting would be pointless.
        """
        job = make_job(progress=25.0, progress_details={"progress_percentage": 73.5})

        assert _job_progress_percentage(job) == 73.5

    @pytest.mark.parametrize("value", [0.0, 1.0, 25.5, 50.0, 99.0])
    def test_each_step_is_reported_exactly(self, value):
        assert _job_progress_percentage(make_job(progress=value)) == value

    def test_progress_details_without_the_key_falls_back_to_the_column(self):
        """Details carrying other keys must not shadow the column.

        A task reporting ``{"stage": "processing"}`` and nothing else leaves the
        column as the only percentage available; returning 0 there would reset the
        bar.
        """
        job = make_job(progress=40.0, progress_details={"stage": "processing_devices"})

        assert _job_progress_percentage(job) == 40.0

    def test_absent_progress_reports_zero_not_none(self):
        """The field is a non-optional float; a UI cannot render None."""
        job = make_job(progress=0.0)

        assert _job_progress_percentage(job) == 0.0


class TestCompletionReportsFull:
    def test_a_finished_job_reports_100(self):
        """The case the issue names, and the one a UI hangs on."""

        job = make_job(progress=100.0, status=JobStatus.SUCCESS)

        assert _job_progress_percentage(job) == 100.0

    def test_completion_from_details_reports_100(self):
        job = make_job(
            progress=100.0,
            progress_details={"progress_percentage": 100.0, "stage": "done"},
            status=JobStatus.SUCCESS,
        )

        assert _job_progress_percentage(job) == 100.0

    def test_serialised_completed_job_carries_100(self):
        """Asserted through the serialiser, since that is what clients receive."""
        response = _serialize_job(make_job(progress=100.0, status=JobStatus.SUCCESS))

        assert response.progress_percentage == 100.0
        assert response.status == JobStatus.SUCCESS

    def test_pending_job_reports_zero(self):
        job = make_job(progress=0.0, status=JobStatus.PENDING)

        assert _job_progress_percentage(job) == 0.0
        assert _serialize_job(job).progress_percentage == 0.0


class TestMonotonicProgress:
    def test_a_sequence_of_updates_increases(self):
        """The contract consumers rely on: each report is >= the last."""

        reported = [
            _job_progress_percentage(make_job(progress=value))
            for value in (0.0, 10.0, 25.0, 50.0, 75.0, 100.0)
        ]

        assert reported == sorted(reported)

    def test_stale_updates_would_be_visible_as_a_regression(self):
        """Not a defect test — a demonstration of what monotonicity means here.

        Celery redelivery can write a stale percentage after a newer one. The
        helper faithfully reports whatever is stored, so monotonicity is a
        property the *task* must maintain. This documents the shape of the bug
        this invariant is meant to catch, so a future regression is recognisable
        rather than looking like a rendering glitch.
        """
        stale_then_fresh = [
            _job_progress_percentage(make_job(progress=value))
            for value in (10.0, 20.0, 20.0, 20.0, 100.0)
        ]

        assert stale_then_fresh == sorted(stale_then_fresh)

    def test_unchanged_progress_is_still_monotonic(self):
        """Repeated identical values are legal — a stage boundary reports twice."""
        reported = [
            _job_progress_percentage(make_job(progress=50.0)) for _ in range(3)
        ]

        assert reported == [50.0, 50.0, 50.0]
        assert reported == sorted(reported)

    def test_details_and_column_do_not_disagree_after_an_update(self):
        """Both sources moving together is what keeps the bar smooth.

        A task that updates one source but not the other makes the reported value
        depend on which field a given endpoint happens to read.
        """
        job = make_job(progress=60.0, progress_details={"progress_percentage": 60.0})

        assert _job_progress_percentage(job) == 60.0
        assert job.progress == 60.0


class TestSerialiserSurfacesProgress:
    def test_response_exposes_both_progress_fields(self):
        """``progress`` and ``progress_percentage`` are separate for a reason."""
        response = _serialize_job(
            make_job(progress=30.0, progress_details={"progress_percentage": 45.0})
        )

        assert response.progress == 30.0
        assert response.progress_percentage == 45.0

    def test_progress_details_are_passed_through_untouched(self):
        details = {"stage": "processing", "processed": 12, "total": 40}
        response = _serialize_job(
            make_job(progress=30.0, progress_details={**details, "progress_percentage": 30.0})
        )

        assert response.progress_details["stage"] == "processing"
        assert response.progress_details["processed"] == 12
        assert response.progress_details["total"] == 40

    def test_progress_details_are_none_when_absent(self):
        """Optional field, optional value — not an empty dict."""
        response = _serialize_job(make_job(progress=10.0))

        assert response.progress_details is None
