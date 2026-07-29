"""Tests for BE-W5-045 (Webhook disaster-recovery replay),
BE-W5-051 (worker startup health probes / queue binding verification) and
BE-W5-054 (poison-message quarantine flow).

These are kept in a single module because all three were delivered in one PR
and share the DR/observability story.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

import pytest

from app.models.job import Job, JobStatus, JobType
from app.models.webhook import (
    Webhook,
    WebhookDelivery,
    WebhookDeliveryStatus,
    WebhookEvent,
)


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _make_webhook(db, name="dr-replay"):
    webhook = Webhook(
        name=f"{name}-{id(db)}",
        url="https://example.com/webhook",
        secret="secret",
        events='["sla.violation"]',
        max_retries=3,
    )
    db.add(webhook)
    db.commit()
    db.refresh(webhook)
    return webhook


def _make_delivery(
    db,
    webhook,
    status,
    event_timestamp,
    idempotency_key=None,
    dead_lettered_at=None,
    response_status_code=502,
):
    idempotency_key = idempotency_key or hashlib.sha256(
        f"{webhook.id}:sla.violation:{event_timestamp.isoformat()}".encode()
    ).hexdigest()
    delivery = WebhookDelivery(
        webhook_id=webhook.id,
        event=WebhookEvent.SLA_VIOLATION,
        payload="{}",
        status=status,
        attempt_count=3,
        response_status_code=response_status_code,
        error_message="simulated failure",
        idempotency_key=idempotency_key,
        event_timestamp=event_timestamp,
        dead_lettered_at=dead_lettered_at,
    )
    db.add(delivery)
    db.commit()
    db.refresh(delivery)
    return delivery


# --------------------------------------------------------------------------- #
# BE-W5-045: Webhook disaster-recovery replay                                 #
# --------------------------------------------------------------------------- #


def test_recover_deliveries_in_window_replays_dead_letter_rows(client, db):
    webhook = _make_webhook(db, "dr-1")
    in_window = datetime.utcnow() - timedelta(minutes=10)
    _make_delivery(
        db, webhook,
        WebhookDeliveryStatus.DEAD_LETTER,
        in_window,
        dead_lettered_at=datetime.utcnow() - timedelta(minutes=5),
    )
    _make_delivery(
        db, webhook,
        WebhookDeliveryStatus.DEAD_LETTER,
        in_window - timedelta(minutes=30),  # outside the window
        dead_lettered_at=datetime.utcnow() - timedelta(minutes=50),
    )

    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.text = "ok"
    mock_response.is_success = True

    from app.services.webhook_service import recover_deliveries_in_window

    with patch("httpx.Client") as mock_client:
        mock_client.return_value.__enter__.return_value.post.return_value = (
            mock_response
        )
        result = recover_deliveries_in_window(
            db,
            start_time=in_window - timedelta(minutes=1),
            end_time=in_window + timedelta(minutes=1),
        )

    assert result["replayed"] == 1
    assert result["total"] == 1
    # The delivery inside the window should now be SUCCESS; idempotency preserved.
    surviving = (
        db.query(WebhookDelivery)
        .filter(WebhookDelivery.webhook_id == webhook.id)
        .order_by(WebhookDelivery.event_timestamp.asc())
        .all()
    )
    success_rows = [d for d in surviving if d.status == WebhookDeliveryStatus.SUCCESS]
    assert len(success_rows) == 1
    success = success_rows[0]
    expected_key = hashlib.sha256(
        f"{webhook.id}:sla.violation:{in_window.isoformat()}".encode()
    ).hexdigest()
    assert success.idempotency_key == expected_key
    # Note: stays SUCCESS-equivalent through replay; idempotency key persisted unchanged.


def test_recover_deliveries_in_window_rejects_inverted_window(client, db):
    from app.services.webhook_service import recover_deliveries_in_window
    now = datetime.utcnow()
    with pytest.raises(ValueError):
        recover_deliveries_in_window(db, start_time=now, end_time=now - timedelta(hours=1))


def test_dr_replay_endpoint_validates_window(client):
    response = client.post(
        "/webhooks/disaster-recovery/replay",
        json={
            "start_time": "2026-06-01T00:00:00",
            "end_time": "2026-05-31T00:00:00",
        },
    )
    assert response.status_code == 422  # pydantic validation error


def test_enqueue_webhook_dr_replay_creates_job(db):
    from app.services.webhook_service import enqueue_webhook_dr_replay

    start = datetime.utcnow() - timedelta(hours=2)
    end = datetime.utcnow()
    with patch(
        "app.tasks.webhook_tasks.recover_webhooks_in_window.apply_async",
        return_value=Mock(id="celery-task-id-1"),
    ):
        job = enqueue_webhook_dr_replay(db, start_time=start, end_time=end)
    db.refresh(job)
    assert job.job_type == JobType.WEBHOOK_DR_REPLAY
    assert job.celery_task_id == "celery-task-id-1"
    payload = json.loads(job.payload)
    assert payload["start_time"].startswith(start.isoformat()[:10])


# --------------------------------------------------------------------------- #
# BE-W5-051: Worker startup health probes / queue bindings                    #
# --------------------------------------------------------------------------- #


def test_verify_queue_bindings_reports_missing(client):
    from app.core.config import settings
    original_required = settings.CELERY_REQUIRED_QUEUES

    try:
        settings.CELERY_REQUIRED_QUEUES = "missing-queue-xyz"

        fake_inspect = Mock()
        fake_inspect.active_queues.return_value = {
            "worker@host": [{"name": "celery"}],
        }
        from app.tasks import celery_app as celery_app_mod

        with patch.object(celery_app_mod.celery_app.control, "inspect", return_value=fake_inspect):
            probe = celery_app_mod.verify_queue_bindings(strict=False)

        assert probe["ok"] is False
        assert "missing-queue-xyz" in probe["missing"]
        assert "celery" in probe["observed"]
        assert probe["workers_seen"] == 1
    finally:
        settings.CELERY_REQUIRED_QUEUES = original_required


def test_verify_queue_bindings_passes_when_all_present():
    from app.core.config import settings
    original_required = settings.CELERY_REQUIRED_QUEUES

    try:
        settings.CELERY_REQUIRED_QUEUES = "celery,webhooks"

        fake_inspect = Mock()
        fake_inspect.active_queues.return_value = {
            "worker@host": [
                {"name": "celery"},
                {"name": "webhooks"},
            ],
        }
        from app.tasks import celery_app as celery_app_mod

        with patch.object(celery_app_mod.celery_app.control, "inspect", return_value=fake_inspect):
            probe = celery_app_mod.verify_queue_bindings(strict=False)

        assert probe["ok"] is True
        assert probe["missing"] == []
        assert set(probe["observed"]) == {"celery", "webhooks"}
    finally:
        settings.CELERY_REQUIRED_QUEUES = original_required


def test_verify_queue_bindings_strict_raises_on_missing():
    from app.core.config import settings
    original_required = settings.CELERY_REQUIRED_QUEUES

    try:
        settings.CELERY_REQUIRED_QUEUES = "must-exist"

        fake_inspect = Mock()
        fake_inspect.active_queues.return_value = {}
        from app.tasks import celery_app as celery_app_mod

        with patch.object(celery_app_mod.celery_app.control, "inspect", return_value=fake_inspect):
            with pytest.raises(RuntimeError) as exc_info:
                celery_app_mod.verify_queue_bindings()
        assert "must-exist" in str(exc_info.value)
    finally:
        settings.CELERY_REQUIRED_QUEUES = original_required


def test_worker_health_endpoint_returns_503_when_missing(monkeypatch):
    from app.tasks import celery_app as celery_app_mod

    monkeypatch.setattr(celery_app_mod, "verify_queue_bindings", lambda **_: {
        "ok": False,
        "required": ["celery"],
        "observed": [],
        "missing": ["celery"],
        "workers_seen": 0,
        "timeout_seconds": 1.0,
    })
    # Drive the helper that the route consumes; each route call re-imports.
    from app.main import check_worker_queue_bindings
    import asyncio
    probe = asyncio.run(check_worker_queue_bindings())
    assert probe["ok"] is False
    assert "celery" in probe["missing"]


def test_worker_health_endpoint_returns_503_via_route(monkeypatch, client):
    """Drive the actual /health/worker route to exercise status-code logic."""
    from app.tasks import celery_app as celery_app_mod

    monkeypatch.setattr(celery_app_mod, "verify_queue_bindings", lambda **_: {
        "ok": False,
        "required": ["celery"],
        "observed": [],
        "missing": ["celery"],
        "workers_seen": 0,
        "timeout_seconds": 1.0,
    })

    response = client.get("/health/worker")
    assert response.status_code == 503
    payload = response.json()
    assert payload["status"] == "missing_queues"
    assert "celery" in payload["missing"]


def test_worker_health_endpoint_returns_200_when_ok(monkeypatch, client):
    from app.tasks import celery_app as celery_app_mod

    monkeypatch.setattr(celery_app_mod, "verify_queue_bindings", lambda **_: {
        "ok": True,
        "required": ["celery"],
        "observed": ["celery"],
        "missing": [],
        "workers_seen": 1,
        "timeout_seconds": 1.0,
    })

    response = client.get("/health/worker")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


# --------------------------------------------------------------------------- #
# BE-W5-054: Poison-message quarantine                                        #
# --------------------------------------------------------------------------- #


def test_database_task_marks_quarantine_on_retry_exhaustion():
    """The DatabaseTask base should auto-quarantine a job that has
    exhausted its max_retries budget."""
    from app.tasks.sla_tasks import DatabaseTask, _hash_job_payload

    payload = json.dumps({"device_id": "dev-1", "period": "2026-Q1"}, sort_keys=True)
    job = Job(
        celery_task_id="celery-1",
        job_type=JobType.SLA_COMPUTATION,
        payload=payload,
        status=JobStatus.FAILURE,
        retry_count=3,
        max_retries=3,
    )

    class _S:
        def __init__(self, j):
            self.j = j

        def query(self, _model):
            m = Mock()
            m.filter.return_value.first.return_value = self.j
            return m

        def commit(self):
            pass

    db = _S(job)
    task = DatabaseTask()
    task._mark_failure(db, "celery-1", "ValueError('boom')")

    assert job.status == JobStatus.QUARANTINED
    assert job.payload_hash == _hash_job_payload(job)
    assert job.quarantine_reason == "ValueError('boom')"
    assert job.quarantined_at is not None


def test_database_task_does_not_quarantine_below_retry_cap():
    from app.tasks.sla_tasks import DatabaseTask

    job = Job(
        celery_task_id="celery-2",
        job_type=JobType.SLA_COMPUTATION,
        payload="{}",
        status=JobStatus.FAILURE,
        retry_count=1,
        max_retries=3,
    )

    class _S:
        def __init__(self, j):
            self.j = j

        def query(self, _model):
            m = Mock()
            m.filter.return_value.first.return_value = self.j
            return m

        def commit(self):
            pass

    db = _S(job)
    DatabaseTask()._mark_failure(db, "celery-2", "transient")

    assert job.status == JobStatus.FAILURE
    assert job.quarantine_reason is None
    assert job.quarantined_at is None


def test_list_quarantined_jobs_returns_only_quarantined(client):
    from app.api.v1.endpoints.jobs import list_quarantined_jobs

    quarantined = Job(
        celery_task_id="qa-1",
        job_type=JobType.SLA_COMPUTATION,
        payload='{"device_id":"dev-1"}',
        status=JobStatus.QUARANTINED,
        retry_count=3,
        max_retries=3,
        payload_hash=hashlib.sha256(b'{"device_id":"dev-1"}').hexdigest(),
        quarantine_reason="ValueError",
        quarantined_at=datetime.utcnow(),
    )
    other = Job(
        celery_task_id="qa-2",
        job_type=JobType.SLA_COMPUTATION,
        payload='{}',
        status=JobStatus.SUCCESS,
    )
    from app.db.session import SessionLocal
    db_session = SessionLocal()
    try:
        db_session.add_all([quarantined, other])
        db_session.commit()
        result = list_quarantined_jobs(
            limit=10,
            current_user=Mock(),
            db=db_session,
        )
        assert len(result) == 1
        assert result[0].celery_task_id == "qa-1"
        assert result[0].payload_hash
    finally:
        db_session.close()
