"""Tests for issues #543 and #544 (Celery progress + worker pool tuning).

- #543: tasks publish ``update_state(state="PROGRESS", meta={current,total,
  progress_percentage})`` and the jobs API surfaces ``progress_percentage``.
- #544: ``app.tasks.concurrency_config.get_task_pool_config`` splits task
  types into an I/O-bound (eventlet/50) and a CPU-bound (prefork/4) pool.
"""

import unittest
from unittest.mock import patch
from uuid import uuid4

from app.api.v1.endpoints.jobs import (
    JobProgressResponse,
    JobResponse,
    _job_progress_percentage,
)
from app.core.config import settings
from app.models.job import Job, JobStatus, JobType
from app.tasks.concurrency_config import get_task_pool_config
from app.tasks.sla_tasks import (
    DatabaseTask,
    compute_bulk_sla,
    compute_sla_chunk,
    compute_sla_for_device,
)


class FakeRedis:
    """Minimal in-memory stand-in for the redis client used by task locks."""

    def __init__(self):
        self.store = {}
        self.ops_fail = False

    def set(self, key, value, nx=False, ex=None):
        if self.ops_fail:
            raise ConnectionError("connection refused")
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True

    def get(self, key):
        if self.ops_fail:
            raise ConnectionError("connection refused")
        return self.store.get(key)

    def delete(self, key):
        if self.ops_fail:
            raise ConnectionError("connection refused")
        self.store.pop(key, None)
        return 1


class FakeSession:
    """Session whose job lookups always miss, so job-row bookkeeping is a no-op."""

    def query(self, *args, **kwargs):
        return self

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return None

    def commit(self):
        pass

    def close(self):
        pass

    def rollback(self):
        pass

    def add(self, *args, **kwargs):
        pass

    def refresh(self, *args, **kwargs):
        pass

    def execute(self, *args, **kwargs):
        return self

    def scalar(self):
        return True


class _ProgressProbe(DatabaseTask):
    """Concrete DatabaseTask subclass used to exercise ``_publish_progress``."""

    name = "tests.progress_probe"
    abstract = False


class _TaskInfraMixin(unittest.TestCase):
    """Shared task-execution scaffolding for #543 tests."""

    def setUp(self):
        self.fake_redis = FakeRedis()
        self.redis_patcher = patch(
            "app.services.task_lock._redis_client", return_value=self.fake_redis
        )
        self.redis_patcher.start()
        self.session_patcher = patch(
            "app.tasks.sla_tasks.SessionLocal", return_value=FakeSession()
        )
        self.session_patcher.start()
        self.addCleanup(self.redis_patcher.stop)
        self.addCleanup(self.session_patcher.stop)

    def _sla_result(self, device_id, period, violated=False):
        return {
            "device_id": device_id,
            "period": period,
            "availability": 95.0 if violated else 99.5,
            "is_violated": violated,
            "mttr_minutes": 30,
        }

    def _progress_calls(self, update_state_mock):
        """Filter recorded update_state calls down to PROGRESS state ones."""
        return [
            c.kwargs
            for c in update_state_mock.call_args_list
            if c.kwargs.get("state") == "PROGRESS"
        ]


class TestProgressPublishingInLoopTasks(_TaskInfraMixin):
    """Issue #543 — loop tasks publish per-item PROGRESS state updates."""

    @patch("app.tasks.sla_tasks.DatabaseTask.update_state")
    @patch("app.services.sla_service.compute_device_sla")
    def test_compute_sla_chunk_publishes_current_total_per_device(
        self, mock_sla, mock_update_state
    ):
        mock_sla.side_effect = lambda db, device_id, period: self._sla_result(
            device_id, period
        )

        result = compute_sla_chunk(
            chunk_device_ids=["dev-1", "dev-2", "dev-3", "dev-4"],
            period="2024-01",
            job_task_id=None,
        )

        self.assertEqual(result["total"], 4)
        progress_calls = self._progress_calls(mock_update_state)
        self.assertGreaterEqual(len(progress_calls), 4)
        # The last published state has processed all devices.
        meta = progress_calls[-1]["meta"]
        self.assertEqual(meta["current"], 4)
        self.assertEqual(meta["total"], 4)
        self.assertEqual(meta["progress_percentage"], 100.0)
        self.assertEqual(meta["stage"], "processing_chunk")
        # Per-item counters are present in the meta.
        self.assertIn("current_device", meta)
        self.assertEqual(meta["processed_count"], 4)

    @patch("app.tasks.sla_tasks.DatabaseTask.update_state")
    @patch("app.services.sla_service.compute_device_sla")
    def test_compute_bulk_sla_publishes_per_item_and_stage_progress(
        self, mock_sla, mock_update_state
    ):
        mock_sla.side_effect = lambda db, device_id, period: self._sla_result(
            device_id, period
        )

        result = compute_bulk_sla(device_ids=["dev-1", "dev-2"], period="2024-01")

        self.assertEqual(result["total"], 2)
        progress_calls = self._progress_calls(mock_update_state)

        stages = {call["meta"].get("stage") for call in progress_calls}
        self.assertIn("initialization", stages)
        self.assertIn("processing_devices", stages)
        self.assertIn("finalizing", stages)

        per_device = [
            c["meta"] for c in progress_calls if c["meta"].get("stage") == "processing_devices"
        ]
        per_device.sort(key=lambda c: c["current"])
        self.assertEqual(per_device[0]["current"], 1)
        self.assertEqual(per_device[0]["total"], 2)
        self.assertEqual(per_device[0]["progress_percentage"], 50.0)
        self.assertEqual(per_device[1]["current"], 2)
        self.assertEqual(per_device[1]["progress_percentage"], 100.0)


class TestProgressPublishingInSingleTask(_TaskInfraMixin):
    """Issue #543 — compute_sla_for_device publishes stage-based progress."""

    @patch("app.tasks.sla_tasks.DatabaseTask.update_state")
    @patch("app.services.sla_service.compute_device_sla")
    @patch("app.services.webhook_service.trigger_sla_violation_webhooks")
    def test_publishes_progress_across_stages(
        self, mock_webhooks, mock_sla, mock_update_state
    ):
        mock_sla.return_value = self._sla_result("dev-1", "2024-01", violated=True)
        mock_webhooks.return_value = []

        result = compute_sla_for_device(device_id="dev-1", period="2024-01")

        self.assertEqual(result["device_id"], "dev-1")
        progress_calls = self._progress_calls(mock_update_state)
        self.assertGreaterEqual(len(progress_calls), 4)
        for call in progress_calls:
            self.assertIn("current", call["meta"])
            self.assertIn("total", call["meta"])
            self.assertIn("progress_percentage", call["meta"])
        stages = {c["meta"].get("stage") for c in progress_calls}
        self.assertEqual(
            stages,
            {
                "data_collection",
                "sla_computation_complete",
                "triggering_webhooks",
                "finalizing",
            },
        )

    @patch(
        "app.tasks.sla_tasks.DatabaseTask.update_state",
        side_effect=RuntimeError("result backend unreachable"),
    )
    @patch("app.services.sla_service.compute_device_sla")
    def test_eager_mode_survives_update_state_failure(self, mock_sla, mock_update_state):
        """update_state raising (eager/no-backend) must not break the task."""
        mock_sla.return_value = self._sla_result("dev-1", "2024-01")

        result = compute_sla_for_device(device_id="dev-1", period="2024-01")

        self.assertEqual(result["device_id"], "dev-1")
        self.assertTrue(mock_update_state.called)


class TestPublishProgressHelper(unittest.TestCase):
    """Issue #543 — DatabaseTask._publish_progress meta construction."""

    def setUp(self):
        self.update_state_patcher = patch(
            "app.tasks.sla_tasks.DatabaseTask.update_state"
        )
        self.mock_update_state = self.update_state_patcher.start()
        self.addCleanup(self.update_state_patcher.stop)

    def test_meta_contains_current_total_and_percentage(self):
        _ProgressProbe()._publish_progress(current=1, total=4, stage="processing")

        call = self.mock_update_state.call_args
        self.assertEqual(call.kwargs.get("state"), "PROGRESS")
        meta = call.kwargs.get("meta")
        self.assertEqual(meta["current"], 1)
        self.assertEqual(meta["total"], 4)
        self.assertEqual(meta["progress_percentage"], 25.0)
        self.assertEqual(meta["stage"], "processing")

    def test_percentage_is_rounded_to_two_decimals(self):
        _ProgressProbe()._publish_progress(current=1, total=3)

        meta = self.mock_update_state.call_args.kwargs["meta"]
        self.assertEqual(meta["progress_percentage"], round((1 / 3) * 100, 2))

    def test_skips_publish_when_total_is_zero(self):
        _ProgressProbe()._publish_progress(current=0, total=0)
        self.mock_update_state.assert_not_called()


class TestJobProgressPercentageExposed(unittest.TestCase):
    """Issue #543 — jobs API exposes a clear ``progress_percentage``."""

    def test_helper_prefers_progress_details_percentage(self):
        job = Job(progress=12.0, progress_details={"progress_percentage": 42.2})
        self.assertEqual(_job_progress_percentage(job), 42.2)

    def test_helper_falls_back_to_progress_column(self):
        job = Job(progress=33.0, progress_details=None)
        self.assertEqual(_job_progress_percentage(job), 33.0)

    def test_job_response_carries_progress_percentage_field(self):
        resp = JobResponse(
            id=uuid4(),
            celery_task_id="task-1",
            job_type=JobType.SLA_COMPUTATION,
            status=JobStatus.STARTED,
            progress=40.0,
            progress_percentage=44.2,
            created_at="2026-01-01T00:00:00.000Z",
        )
        self.assertEqual(resp.progress_percentage, 44.2)

    def test_job_progress_response_carries_progress_percentage_field(self):
        resp = JobProgressResponse(
            id=uuid4(),
            status=JobStatus.STARTED,
            progress=40.0,
            progress_percentage=44.2,
        )
        self.assertEqual(resp.progress_percentage, 44.2)


class TestConcurrencyConfig(unittest.TestCase):
    """Issue #544 — per-task-type worker pool configuration."""

    def test_webhook_task_resolves_to_io_pool(self):
        config = get_task_pool_config("app.tasks.webhook_tasks.dispatch_webhook_delivery")
        self.assertEqual(config["pool"], settings.CELERY_WEBHOOK_POOL)
        self.assertEqual(config["concurrency"], settings.CELERY_IO_CONCURRENCY)

    def test_webhook_named_task_resolves_to_eventlet_50(self):
        config = get_task_pool_config("app.tasks.webhook_tasks.something")
        self.assertEqual(config["pool"], "eventlet")
        self.assertEqual(config["concurrency"], 50)

    def test_calculation_task_resolves_to_cpu_pool(self):
        config = get_task_pool_config("app.tasks.sla_tasks.compute_bulk_sla")
        self.assertEqual(config["pool"], settings.CELERY_CALC_POOL)
        self.assertEqual(config["concurrency"], settings.CELERY_CPU_CONCURRENCY)

    def test_calculate_named_task_resolves_to_prefork_4(self):
        config = get_task_pool_config("app.tasks.contract_tasks.something_calculate")
        self.assertEqual(config["pool"], "prefork")
        self.assertEqual(config["concurrency"], 4)

    def test_unknown_task_falls_back_to_cpu_pool(self):
        config = get_task_pool_config("app.tasks.misc_tasks.something_else")
        self.assertEqual(config["pool"], "prefork")
        self.assertEqual(config["concurrency"], 4)

    def test_settings_expose_concurrency_defaults(self):
        self.assertEqual(settings.CELERY_IO_CONCURRENCY, 50)
        self.assertEqual(settings.CELERY_CPU_CONCURRENCY, 4)
        self.assertEqual(settings.CELERY_WEBHOOK_POOL, "eventlet")
        self.assertEqual(settings.CELERY_CALC_POOL, "prefork")


if __name__ == "__main__":
    unittest.main()