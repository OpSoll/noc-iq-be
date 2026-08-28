"""
Tests for Celery observability issues (Stellar Wave #534, #535, #536, #537).

- #534: Flower monitoring dashboard in docker-compose + README
- #535: Exponential retry backoff on webhook dispatch task
- #536: Worker heartbeat monitor (ping_celery_workers beat task + health endpoint)
- #537: Celery task Prometheus metrics on /metrics
"""

import json
import logging
from unittest.mock import MagicMock, patch
from uuid import uuid4

import httpx
import pytest
import yaml

from app.tasks.celery_app import celery_app
from app.tasks.webhook_tasks import (
    _retry_backoff_countdown,
    dispatch_webhook_delivery,
)
from app.metrics import celery_metrics


# --------------------------------------------------------------------------- #
# #535 – Exponential retry backoff on webhook dispatch                        #
# --------------------------------------------------------------------------- #

def test_webhook_dispatch_retry_configuration():
    """Task must configure autoretry, backoff, backoff max and max retries."""
    assert dispatch_webhook_delivery.autoretry_for == (httpx.RequestError,)
    assert dispatch_webhook_delivery.retry_backoff is True
    assert dispatch_webhook_delivery.retry_backoff_max == 600
    assert dispatch_webhook_delivery.max_retries == 5


def test_retry_backoff_countdown_sequence():
    """Backoff delays grow exponentially: 5s, 15s, 45s, ~2m, capped at 600s."""
    assert _retry_backoff_countdown(0) == 5
    assert _retry_backoff_countdown(1) == 15
    assert _retry_backoff_countdown(2) == 45
    assert _retry_backoff_countdown(3) == 135  # ~2m
    assert _retry_backoff_countdown(4) == 405
    assert _retry_backoff_countdown(10) == 600  # capped at retry_backoff_max


class _RetrySignal(Exception):
    """Raised by the fake ``self.retry`` to capture retry call kwargs."""

    def __init__(self, exc, countdown):
        self.exc = exc
        self.countdown = countdown
        super().__init__()


class _FakeRetryRequest:
    retries = 2
    id = "task-retry-123"


class _FakeRetrySelf:
    max_retries = 5
    request = _FakeRetryRequest()

    def retry(self, exc=None, countdown=None, **kwargs):
        raise _RetrySignal(exc=exc, countdown=countdown)


def test_webhook_dispatch_retry_uses_backoff_and_logs_eta(caplog):
    """Failed dispatch logs attempt number + next retry ETA and retries with the
    exponential countdown."""
    delivery_id = str(uuid4())
    # ``task.run`` is wrapped by autoretry and bound to the task instance, so
    # invoke the unwrapped function with a fake ``self`` to exercise the retry
    # path deterministically.
    raw_run = dispatch_webhook_delivery._orig_run.__func__
    with patch("app.tasks.webhook_tasks.SessionLocal", return_value=MagicMock()), \
         patch("app.services.webhook_service.dispatch_delivery", side_effect=Exception("boom")), \
         patch("app.tasks.webhook_tasks.audit_log.log_event") as mock_audit, \
         pytest.raises(_RetrySignal) as exc_info:
        with caplog.at_level(logging.WARNING, logger="app.tasks.webhook_tasks"):
            raw_run(_FakeRetrySelf(), delivery_id)

    # Retry countdown for request.retries == 2 -> 45s
    assert exc_info.value.countdown == 45

    # Logged the retry attempt number and the next retry ETA
    assert "(attempt 3/5)" in caplog.text
    assert "in 45s" in caplog.text
    assert "next retry ETA:" in caplog.text

    # Audit event carries the retry delay and ETA
    details = mock_audit.call_args.kwargs["details"]
    assert details["retry_count"] == 3
    assert details["retry_delay_seconds"] == 45
    assert "next_retry_eta" in details


# --------------------------------------------------------------------------- #
# #534 – Flower monitoring dashboard in docker-compose                        #
# --------------------------------------------------------------------------- #

def test_flower_service_in_docker_compose():
    with open("docker-compose.yml") as fh:
        compose = yaml.safe_load(fh)

    flower = compose["services"]["flower"]
    assert flower["command"].startswith("celery -A app.tasks.celery_app flower")
    assert "--basic_auth=" in flower["command"]
    assert flower["ports"][0].endswith(":5555")
    assert "redis" in flower["depends_on"]
    assert "celery-worker" in flower["depends_on"]


def test_flower_dependency_and_readme_docs():
    requirements = open("requirements.txt").read()
    assert "flower==" in requirements

    readme = open("README.md").read()
    assert "Flower" in readme
    assert "5555" in readme
    assert "FLOWER_USER" in readme


# --------------------------------------------------------------------------- #
# #536 – Worker heartbeat monitor                                             #
# --------------------------------------------------------------------------- #

def test_ping_celery_workers_beat_schedule_registered():
    entry = celery_app.conf.beat_schedule["ping-celery-workers"]
    assert entry["task"] == "app.tasks.worker_health.ping_celery_workers"
    assert entry["schedule"] == 60.0


def test_ping_celery_workers_healthy_path():
    from app.tasks import worker_health

    status = {
        "status": "ok",
        "healthy": True,
        "active_workers": ["celery@worker-1"],
        "worker_count": 1,
        "checked_at": "2026-08-27T00:00:00+00:00",
    }
    with patch.object(worker_health, "check_worker_health", return_value=status), \
         patch.object(worker_health, "store_worker_health", return_value=True) as mock_store, \
         patch.object(worker_health, "_send_alert_webhook") as mock_alert:
        result = worker_health.ping_celery_workers()

    assert result["healthy"] is True
    mock_store.assert_called_once_with(status)
    mock_alert.assert_not_called()


def test_ping_celery_workers_alerts_when_no_worker_responds(caplog):
    from app.tasks import worker_health

    status = {
        "status": "down",
        "healthy": False,
        "active_workers": [],
        "worker_count": 0,
        "checked_at": "2026-08-27T00:00:00+00:00",
    }
    with patch.object(worker_health, "check_worker_health", return_value=status), \
         patch.object(worker_health, "store_worker_health", return_value=True), \
         patch.object(worker_health, "_send_alert_webhook") as mock_alert, \
         caplog.at_level(logging.ERROR, logger="app.tasks.worker_health"):
        result = worker_health.ping_celery_workers()

    assert result["healthy"] is False
    assert "ALERT" in caplog.text
    mock_alert.assert_called_once_with(status)


def test_worker_health_alert_webhook_posts_payload():
    from app.tasks import worker_health

    status = {"status": "down", "healthy": False, "active_workers": [], "worker_count": 0}
    with patch.object(worker_health.settings, "WORKER_ALERT_WEBHOOK_URL", "https://example.com/hook"), \
         patch("httpx.post") as mock_post:
        worker_health._send_alert_webhook(status)

    mock_post.assert_called_once()
    payload = mock_post.call_args.kwargs["json"]
    assert payload["event"] == "celery.workers_down"
    assert payload["details"] == status


def test_detailed_health_endpoint_reports_cached_worker_status(client):
    from app.api.v1.endpoints import health as health_endpoint

    cached = {
        "status": "ok",
        "healthy": True,
        "active_workers": ["celery@worker-1"],
        "worker_count": 1,
        "checked_at": "2026-08-27T00:00:00+00:00",
    }
    with patch.object(health_endpoint, "read_worker_health", return_value=cached):
        response = client.get("/api/v1/health/detailed")

    assert response.status_code == 200
    body = response.json()
    assert body["dependencies"]["celery_workers"]["healthy"] is True
    assert body["dependencies"]["celery_workers"]["worker_count"] == 1
    assert "database" in body["dependencies"]
    assert "celery_broker" in body["dependencies"]


# --------------------------------------------------------------------------- #
# #537 – Celery task Prometheus metrics                                       #
# --------------------------------------------------------------------------- #

def test_celery_metrics_collectors_defined():
    # prometheus_client stores counters without the ``_total`` suffix in
    # ``_name`` and re-appends it at export time.
    assert celery_metrics.CELERY_TASKS_TOTAL._name == "celery_tasks"
    assert celery_metrics.CELERY_TASK_RUNTIME_SECONDS._name == "celery_task_runtime_seconds"
    assert celery_metrics.CELERY_TASKS_FAILED._name == "celery_tasks_failed"

    from prometheus_client import generate_latest
    exported = generate_latest().decode()
    assert "celery_tasks_total" in exported
    assert "celery_task_runtime_seconds" in exported
    assert "celery_tasks_failed" in exported


def test_celery_metrics_exported_on_metrics_endpoint():
    from fastapi.testclient import TestClient
    from app.main import app

    response = TestClient(app).get("/metrics")
    assert response.status_code == 200
    assert "celery_tasks_total" in response.text
    assert "celery_task_runtime_seconds" in response.text
    assert "celery_tasks_failed" in response.text


def test_celery_task_signals_record_metrics():
    """Running a task eagerly (via apply) updates the Prometheus metrics."""
    original_eager = celery_app.conf.task_always_eager
    original_store = celery_app.conf.task_store_eager_result
    celery_app.conf.task_always_eager = True
    # Do not store eager results: the configured backend is Redis and tests
    # run without a broker, so eager result storage would try to connect.
    celery_app.conf.task_store_eager_result = False

    try:
        @celery_app.task(name="test.metrics.success.task")
        def ok_task():
            return 1

        @celery_app.task(name="test.metrics.failure.task")
        def fail_task():
            raise ValueError("boom")

        total = celery_metrics.CELERY_TASKS_TOTAL.labels(task="test.metrics.success.task")
        runtime = celery_metrics.CELERY_TASK_RUNTIME_SECONDS.labels(task="test.metrics.success.task")
        failed = celery_metrics.CELERY_TASKS_FAILED.labels(task="test.metrics.failure.task")

        before_total = total._value.get()
        before_sum = runtime._sum.get()

        ok_task.apply()
        assert total._value.get() == before_total + 1
        assert runtime._sum.get() > before_sum

        before_failed = failed._value.get()
        # In eager mode without result storage the failure is logged (and the
        # task_failure signal fired) rather than re-raised to the caller.
        fail_task.apply()
        assert failed._value.get() == before_failed + 1
    finally:
        celery_app.conf.task_always_eager = original_eager
        celery_app.conf.task_store_eager_result = original_store
