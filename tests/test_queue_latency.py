"""Unit tests for queue latency tracking (#899)."""

import pytest

from app.services import queue_latency
from app.services.queue_latency import QueueLatencyTracker


@pytest.fixture
def recorded(monkeypatch):
    """Capture histogram calls instead of writing to the global registry."""
    calls = []
    monkeypatch.setattr(
        queue_latency.metrics,
        "record_histogram",
        lambda name, value, tags=None: calls.append((name, value, tags)),
    )
    return calls


def test_mark_started_records_queue_wait(monkeypatch, recorded):
    times = iter([100.0, 100.25])
    monkeypatch.setattr(queue_latency.time, "time", lambda: next(times))

    tracker = QueueLatencyTracker("sla")
    tracker.mark_enqueued()
    tracker.mark_started()

    name, value, tags = recorded[0]
    assert name == "sla_queue_wait_ms"
    assert value == pytest.approx(250.0)
    assert tags == {"job_type": "sla"}


def test_mark_finished_records_execution_duration(monkeypatch, recorded):
    times = iter([100.0, 100.1, 100.6])
    monkeypatch.setattr(queue_latency.time, "time", lambda: next(times))

    tracker = QueueLatencyTracker("webhook")
    tracker.mark_enqueued()
    tracker.mark_started()
    tracker.mark_finished()

    assert [call[0] for call in recorded] == [
        "webhook_queue_wait_ms",
        "webhook_execution_ms",
    ]
    assert recorded[1][1] == pytest.approx(500.0)


def test_mark_started_without_enqueue_records_nothing(recorded):
    tracker = QueueLatencyTracker("sla")

    tracker.mark_started()

    assert recorded == []


def test_mark_finished_without_start_records_nothing(recorded):
    tracker = QueueLatencyTracker("sla")

    tracker.mark_finished()

    assert recorded == []


def test_each_sample_is_recorded_independently(monkeypatch, recorded):
    times = iter([1.0, 1.5, 2.0, 2.25])
    monkeypatch.setattr(queue_latency.time, "time", lambda: next(times))

    first = QueueLatencyTracker("sla")
    first.mark_enqueued()
    first.mark_started()

    second = QueueLatencyTracker("sla")
    second.mark_enqueued()
    second.mark_started()

    assert len(recorded) == 2
    assert recorded[0][1] == pytest.approx(500.0)
    assert recorded[1][1] == pytest.approx(250.0)
