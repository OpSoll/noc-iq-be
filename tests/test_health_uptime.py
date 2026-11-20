"""Tests for process uptime and start time in the detailed health payload (#890)."""

import json
from datetime import datetime

import pytest

from app.api.v1.endpoints import health as health_module


@pytest.fixture
def healthy(monkeypatch):
    monkeypatch.setattr(health_module, "_check_database", lambda: True)
    monkeypatch.setattr(health_module, "_check_redis", lambda: True)
    monkeypatch.setattr(health_module, "_check_celery_broker", lambda: True)
    monkeypatch.setattr(health_module, "read_worker_health", lambda: {"status": "ok"})
    monkeypatch.setattr(health_module, "_wallet_health", lambda: {"status": "ok"})


def payload() -> dict:
    return json.loads(health_module.detailed_health().body)


def test_payload_includes_uptime(healthy):
    body = payload()

    assert body["status"] == "ok"
    assert body["uptime_seconds"] >= 0


def test_started_at_is_iso_formatted(healthy):
    started_at = payload()["started_at"]

    assert datetime.fromisoformat(started_at) == health_module._PROCESS_STARTED_AT


def test_values_are_stable_across_requests(healthy):
    first = payload()
    second = payload()

    assert first["started_at"] == second["started_at"]
    assert second["uptime_seconds"] >= first["uptime_seconds"]
