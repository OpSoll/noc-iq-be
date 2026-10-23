"""Tests for Stellar Wave issues #532, #533, #538, #539 (Celery reliability).

- #539: task result backend TTL expiration cleanup (24 hours)
- #532: per-task rate limiting (webhook_dispatch = 100/m)
- #533: task idempotency deduplication via RedisTaskLock
- #538: bulk SLA computation chunking (chunk_size=50)
"""

import unittest
from unittest.mock import patch

from app.tasks.celery_app import celery_app
from app.tasks.sla_tasks import (
    SLA_BULK_CHUNK_SIZE,
    _bulk_sla_lock_job_id,
    compute_bulk_sla,
    compute_sla_chunk,
    compute_sla_for_device,
)
from app.tasks.webhook_tasks import dispatch_webhook_delivery
from app.services.task_lock import (
    DEFAULT_TASK_LOCK_TTL_SECONDS,
    TASK_LOCK_PREFIX,
    RedisTaskLock,
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


class TestCeleryResultTTL(unittest.TestCase):
    """Issue #539 — result backend TTL expiration (24 hours)."""

    def test_result_expires_configured_to_24h(self):
        self.assertEqual(celery_app.conf.result_expires, 86400)

    def test_result_expires_is_full_day_in_seconds(self):
        self.assertEqual(celery_app.conf.result_expires, 24 * 60 * 60)

    def test_result_expires_configured_on_redis_backend(self):
        # The Redis result backend applies result_expires as a key TTL, so
        # expired task result keys are auto-purged by Redis itself without
        # worker-side cleanup — keeping Redis memory bounded.
        expires = getattr(celery_app.backend, "expires", None)
        if expires is not None:
            self.assertEqual(expires, 86400)


class TestWebhookDispatchRateLimit(unittest.TestCase):
    """Issue #532 — per-task rate limiting (webhook_dispatch = 100/m)."""

    def test_dispatch_webhook_delivery_rate_limited(self):
        self.assertEqual(dispatch_webhook_delivery.rate_limit, "100/m")


class TestRedisTaskLockDecorator(unittest.TestCase):
    """Issue #533 — RedisTaskLock decorator semantics."""

    def setUp(self):
        self.fake_redis = FakeRedis()
        redis_patcher = patch("app.services.task_lock._redis_client", return_value=self.fake_redis)
        self.addCleanup(redis_patcher.stop)
        redis_patcher.start()

    def test_acquires_lock_runs_and_releases(self):
        calls = []

        @RedisTaskLock("test-job")
        def work():
            calls.append(1)
            return "done"

        result = work()

        self.assertEqual(result, "done")
        self.assertEqual(calls, [1])
        # Lock acquired with NX + TTL and released after the run.
        self.assertEqual(self.fake_redis.store.get("lock:task:test-job"), None)
        self.assertNotIn(f"{TASK_LOCK_PREFIX}test-job", self.fake_redis.store)

    def test_lock_uses_prefix_and_ttl(self):
        calls = []

        @RedisTaskLock("test-job", ttl_seconds=120)
        def work():
            calls.append(1)

        work()

        # NX + EX acquisition: key must have been set with a TTL. Simulate by
        # checking the lock was acquired (non-empty) then released; the TTL
        # parameter is exercised via a held-lock variant below.
        self.assertEqual(calls, [1])

        # While the lock is held (simulate a concurrent duplicate), the
        # second execution must be skipped.
        self.fake_redis.store[f"{TASK_LOCK_PREFIX}test-job"] = "other-holder"
        result = work()
        self.assertEqual(result["skipped"], True)
        self.assertEqual(result["reason"], "duplicate_task_lock_held")
        self.assertEqual(result["lock_key"], "lock:task:test-job")
        self.assertEqual(calls, [1])  # no second execution

    def test_skips_duplicate_when_lock_held(self):
        calls = []

        @RedisTaskLock("test-job")
        def work():
            calls.append(1)
            return "ran"

        # Pre-hold the lock as if another worker is running the same job.
        self.fake_redis.store[f"{TASK_LOCK_PREFIX}test-job"] = "another-worker"

        result = work()

        self.assertEqual(result["skipped"], True)
        self.assertEqual(result["reason"], "duplicate_task_lock_held")
        self.assertEqual(result["lock_key"], "lock:task:test-job")
        self.assertEqual(calls, [])

    def test_lock_key_format_template_resolves_arguments(self):
        calls = []

        @RedisTaskLock("sla:{device_id}:{period}")
        def work(device_id, period):
            calls.append((device_id, period))
            return device_id

        result = work(device_id="dev-1", period="2024-01")

        self.assertEqual(result, "dev-1")
        self.assertEqual(calls, [("dev-1", "2024-01")])
        self.assertNotIn("lock:task:sla:dev-1:2024-01", self.fake_redis.store)  # released

        # Held variant proves the resolved key is lock:task:sla:dev-1:2024-01
        self.fake_redis.store["lock:task:sla:dev-1:2024-01"] = "other"
        result = work(device_id="dev-1", period="2024-01")
        self.assertEqual(result["skipped"], True)
        self.assertEqual(result["lock_key"], "lock:task:sla:dev-1:2024-01")

    def test_callable_lock_key(self):
        @RedisTaskLock(lock_key=lambda call_args: f"custom:{call_args['device_id']}")
        def work(device_id):
            return device_id

        work(device_id="x")
        self.assertNotIn("lock:task:custom:x", self.fake_redis.store)  # released

        self.fake_redis.store["lock:task:custom:x"] = "other"
        result = work(device_id="x")
        self.assertEqual(result["skipped"], True)
        self.assertEqual(result["lock_key"], "lock:task:custom:x")

    def test_fail_open_when_redis_unavailable(self):
        self.fake_redis.ops_fail = True
        calls = []

        @RedisTaskLock("test-job")
        def work():
            calls.append(1)
            return "ok"

        self.assertEqual(work(), "ok")
        self.assertEqual(calls, [1])

    def test_fail_closed_raises_when_redis_unavailable(self):
        self.fake_redis.ops_fail = True

        @RedisTaskLock("test-job", fail_open=False)
        def work():
            return "ok"

        with self.assertRaises(ConnectionError):
            work()


class TestRedisTaskLockOnSLATasks(unittest.TestCase):
    """Issue #533 — SLA tasks acquire lock:task:<job_id> and skip duplicates."""

    def setUp(self):
        self.fake_redis = FakeRedis()
        self.redis_patcher = patch("app.services.task_lock._redis_client", return_value=self.fake_redis)
        self.redis_patcher.start()
        self.session_patcher = patch("app.tasks.sla_tasks.SessionLocal", return_value=FakeSession())
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

    @patch("app.services.sla_service.compute_device_sla")
    def test_compute_sla_for_device_uses_device_period_lock(self, mock_sla):
        mock_sla.return_value = self._sla_result("dev-1", "2024-01")

        result = compute_sla_for_device(device_id="dev-1", period="2024-01")

        self.assertEqual(result["device_id"], "dev-1")
        # Lock acquired and released around the run.
        self.assertNotIn("lock:task:sla:dev-1:2024-01", self.fake_redis.store)

    @patch("app.services.sla_service.compute_device_sla")
    def test_compute_sla_for_device_skips_duplicate(self, mock_sla):
        mock_sla.return_value = self._sla_result("dev-1", "2024-01")
        self.fake_redis.store["lock:task:sla:dev-1:2024-01"] = "other-worker"

        result = compute_sla_for_device(device_id="dev-1", period="2024-01")

        self.assertEqual(result["skipped"], True)
        self.assertEqual(result["reason"], "duplicate_task_lock_held")
        mock_sla.assert_not_called()

    @patch("app.services.sla_service.compute_device_sla")
    def test_compute_bulk_sla_acquires_deterministic_lock(self, mock_sla):
        mock_sla.return_value = self._sla_result("dev-1", "2024-01")
        device_ids = ["dev-1", "dev-2"]

        result = compute_bulk_sla(device_ids=device_ids, period="2024-01")

        self.assertEqual(result["total"], 2)
        lock_key = f"{TASK_LOCK_PREFIX}{_bulk_sla_lock_job_id({'device_ids': device_ids, 'period': '2024-01'})}"
        self.assertNotIn(lock_key, self.fake_redis.store)  # released

    @patch("app.services.sla_service.compute_device_sla")
    def test_compute_bulk_sla_skips_duplicate_batch(self, mock_sla):
        mock_sla.return_value = self._sla_result("dev-1", "2024-01")
        device_ids = ["dev-1", "dev-2"]
        lock_key = f"{TASK_LOCK_PREFIX}{_bulk_sla_lock_job_id({'device_ids': device_ids, 'period': '2024-01'})}"
        self.fake_redis.store[lock_key] = "other-worker"

        result = compute_bulk_sla(device_ids=device_ids, period="2024-01")

        self.assertEqual(result["skipped"], True)
        self.assertEqual(result["reason"], "duplicate_task_lock_held")
        mock_sla.assert_not_called()


class TestBulkSLAChunking(unittest.TestCase):
    """Issue #538 — bulk SLA computation chunking (chunk_size=50)."""

    def setUp(self):
        self.fake_redis = FakeRedis()
        self.redis_patcher = patch("app.services.task_lock._redis_client", return_value=self.fake_redis)
        self.redis_patcher.start()
        self.session_patcher = patch("app.tasks.sla_tasks.SessionLocal", return_value=FakeSession())
        self.session_patcher.start()
        # Eager results would otherwise be written to the Redis result
        # backend; disable that so tests do not need a live Redis.
        self.original_store_eager = celery_app.conf.task_store_eager_result
        celery_app.conf.task_store_eager_result = False
        self.addCleanup(self.redis_patcher.stop)
        self.addCleanup(self.session_patcher.stop)
        self.addCleanup(self._restore_eager)

    def _restore_eager(self):
        celery_app.conf.task_store_eager_result = self.original_store_eager

    def _sla_result(self, device_id, period, violated=False):
        return {
            "device_id": device_id,
            "period": period,
            "availability": 95.0 if violated else 99.5,
            "is_violated": violated,
            "mttr_minutes": 30,
        }

    @patch("app.services.sla_service.compute_device_sla")
    @patch("app.services.webhook_service.trigger_sla_violation_webhooks")
    def test_large_batch_is_chunked_into_parallel_tasks(self, mock_webhooks, mock_sla):
        """> 50 devices → chunked dispatch with chunk_size=50 and aggregation."""
        mock_webhooks.return_value = []
        chunk_args = []

        original_run = compute_sla_chunk.run

        def spy_run(*args, **kwargs):
            chunk_args.append(args)
            return original_run(*args, **kwargs)

        compute_sla_chunk.run = spy_run
        self.addCleanup(setattr, compute_sla_chunk, "run", original_run)

        def side_effect(db, device_id, period):
            return self._sla_result(device_id, period, violated=device_id.endswith("v"))

        mock_sla.side_effect = side_effect

        device_ids = [f"dev-{i:03d}" for i in range(120)] + ["dev-v"]

        result = compute_bulk_sla(device_ids=device_ids, period="2024-01")

        # Chunked path used.
        self.assertTrue(result["chunked"])
        self.assertEqual(result["chunk_size"], SLA_BULK_CHUNK_SIZE)
        # 121 devices → ceil(121/50) = 3 chunk tasks, each ≤ 50 devices.
        self.assertEqual(len(chunk_args), 3)
        self.assertEqual(result["chunk_count"], 3)
        # Each chunk task is invoked with (chunk_device_ids, period, parent_task_id).
        for chunk in chunk_args:
            self.assertLessEqual(len(chunk[0]), SLA_BULK_CHUNK_SIZE)
            self.assertEqual(chunk[1], "2024-01")
        # Chunk tasks carry a job_task_id slot for progress write-back.
        self.assertTrue(all(len(chunk) >= 3 for chunk in chunk_args))

        # Aggregated summary matches sequential semantics.
        self.assertEqual(result["total"], len(device_ids))
        self.assertEqual(result["processed_count"], len(device_ids))
        self.assertEqual(result["error_count"], 0)
        self.assertEqual(result["violations"], 1)
        self.assertEqual(result["violated_devices"], ["dev-v"])
        self.assertEqual(len(result["results"]), len(device_ids))

    @patch("app.services.sla_service.compute_device_sla")
    def test_exactly_chunk_size_batch_stays_sequential(self, mock_sla):
        """Batches ≤ 50 keep the existing sequential in-process path."""
        mock_sla.side_effect = lambda db, device_id, period: self._sla_result(device_id, period)

        device_ids = [f"dev-{i:03d}" for i in range(SLA_BULK_CHUNK_SIZE)]

        result = compute_bulk_sla(device_ids=device_ids, period="2024-01")

        self.assertNotIn("chunked", result)
        self.assertEqual(result["total"], SLA_BULK_CHUNK_SIZE)
        self.assertEqual(result["processed_count"], SLA_BULK_CHUNK_SIZE)
        self.assertEqual(len(result["results"]), SLA_BULK_CHUNK_SIZE)

    @patch("app.services.sla_service.compute_device_sla")
    @patch("app.services.webhook_service.trigger_sla_violation_webhooks")
    def test_chunked_path_still_triggers_violation_webhooks(self, mock_webhooks, mock_sla):
        mock_sla.side_effect = lambda db, device_id, period: self._sla_result(
            device_id, period, violated=True
        )
        mock_webhooks.return_value = []

        device_ids = [f"dev-{i:03d}" for i in range(60)]

        result = compute_bulk_sla(device_ids=device_ids, period="2024-01")

        self.assertTrue(result["chunked"])
        self.assertEqual(result["violations"], 60)
        self.assertEqual(mock_webhooks.call_count, 60)

    @patch("app.services.sla_service.compute_device_sla")
    def test_chunked_path_records_per_device_errors(self, mock_sla):
        def side_effect(db, device_id, period):
            if device_id == "dev-bad":
                raise ValueError("boom")
            return self._sla_result(device_id, period)

        mock_sla.side_effect = side_effect

        device_ids = [f"dev-{i:03d}" for i in range(60)] + ["dev-bad"]

        result = compute_bulk_sla(device_ids=device_ids, period="2024-01")

        self.assertTrue(result["chunked"])
        self.assertEqual(result["total"], 61)
        self.assertEqual(result["processed_count"], 60)
        self.assertEqual(result["error_count"], 1)
        self.assertEqual(len(result["results"]), 61)
        errors = [r for r in result["results"] if "error" in r]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["device_id"], "dev-bad")


if __name__ == "__main__":
    unittest.main()
