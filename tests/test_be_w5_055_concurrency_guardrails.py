"""Tests for BE-W5-055 — Async worker concurrency tuning profile & safeguards.

Covers:
  * Per-environment profile selection + fallback.
  * Broker stress proxy saturation logic (with mocked celery_app).
  * Guardrail evaluation emits the right gauges + log alerts.
  * Concurrency health endpoint reflects the live state.
"""
from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from app.services import concurrency_guardrails as cg
from app.services.metrics import metrics


# --------------------------------------------------------------------------- #
# Profile selection                                                            #
# --------------------------------------------------------------------------- #


def test_get_profile_known_env_returns_named_profile():
    with patch.object(cg.settings, "APP_ENV", "prod"):
        profile = cg.get_profile()
    assert profile.concurrency == 8
    assert profile.max_tasks == 1000
    assert profile.pool_size == 20


def test_get_profile_unknown_env_falls_back_to_dev():
    with patch.object(cg.settings, "APP_ENV", "qa-mystery"):
        profile = cg.get_profile()
    assert profile == cg.PROFILES["dev"]


def test_get_profile_environment_is_case_insensitive():
    with patch.object(cg.settings, "APP_ENV", "Staging"):
        profile = cg.get_profile()
    assert profile == cg.PROFILES["staging"]


# --------------------------------------------------------------------------- #
# Broker stress proxy                                                          #
# --------------------------------------------------------------------------- #


def test_broker_stress_with_no_celery_app_returns_zero():
    payload = cg.measure_broker_stress(None)
    assert payload == {
        "estimated_connections": 0,
        "max": cg.settings.BROKER_MAX_CONNECTIONS,
        "saturation": 0.0,
        "is_alert": False,
    }


def test_broker_stress_when_no_workers_observed():
    fake_app = MagicMock()
    fake_app.control.inspect.return_value.active_queues.return_value = {}
    payload = cg.measure_broker_stress(fake_app)
    assert payload["estimated_connections"] == 0
    assert payload["is_alert"] is False


def test_broker_stress_when_workers_present_under_threshold():
    fake_app = MagicMock()
    fake_app.control.inspect.return_value.active_queues.return_value = {
        f"w{i}": [{"name": "celery"}] for i in range(2)  # 2 workers
    }
    with patch.object(cg.settings, "CELERY_WORKER_CONCURRENCY", 4), \
         patch.object(cg.settings, "BROKER_MAX_CONNECTIONS", 100):
        payload = cg.measure_broker_stress(fake_app)
    # 2 workers * 4 → 8 estimated connections; saturation = 0.08
    assert payload["estimated_connections"] == 8
    assert payload["saturation"] == pytest.approx(0.08, abs=1e-4)
    assert payload["is_alert"] is False


def test_broker_stress_when_alert_threshold_exceeded():
    fake_app = MagicMock()
    fake_app.control.inspect.return_value.active_queues.return_value = {
        f"w{i}": [{"name": "celery"}] for i in range(10)  # 10 workers
    }
    with patch.object(cg.settings, "CELERY_WORKER_CONCURRENCY", 8), \
         patch.object(cg.settings, "BROKER_MAX_CONNECTIONS", 50), \
         patch.object(cg.settings, "BROKER_SATURATION_THRESHOLD", 0.8):
        payload = cg.measure_broker_stress(fake_app)
    # 10 workers * 8 = 80 > 0.8 * 50 = 40  → alert
    assert payload["estimated_connections"] == 80
    assert payload["is_alert"] is True
    assert payload["saturation"] >= 0.8


def test_broker_stress_swallows_broker_errors_gracefully():
    fake_app = MagicMock()
    fake_app.control.inspect.side_effect = RuntimeError("broker down")
    payload = cg.measure_broker_stress(fake_app)
    assert payload == {
        "estimated_connections": 0,
        "max": cg.settings.BROKER_MAX_CONNECTIONS,
        "saturation": 0.0,
        "is_alert": False,
    }


# --------------------------------------------------------------------------- #
# Guardrail evaluation                                                         #
# --------------------------------------------------------------------------- #


def test_evaluate_guardrails_no_alert_clears_gauges():
    metrics._gauges.clear()  # type: ignore[attr-defined]
    fake_app = MagicMock()
    fake_app.control.inspect.return_value.active_queues.return_value = {
        "w1": [{"name": "celery"}],
    }
    with patch.object(cg.pool_health, "get_stats", return_value={
        "pool_size": 10, "active": 1, "idle": 9, "overflow": 0,
        "max_overflow": 20, "saturation": 0.1,
    }), patch.object(cg.settings, "CELERY_WORKER_CONCURRENCY", 2), \
         patch.object(cg.settings, "DB_GUARDRAIL_THRESHOLD", 0.75), \
         patch.object(cg.settings, "DB_POOL_SATURATION_THRESHOLD", 0.9):
        result = cg.evaluate_guardrails(fake_app, log_alerts=False)
    assert result["alerts_active"] is False
    assert metrics._gauges["guardrail.alert.db"] == 0.0  # type: ignore[attr-defined]
    assert metrics._gauges["guardrail.alert.broker"] == 0.0  # type: ignore[attr-defined]


def test_evaluate_guardrails_alerts_above_guardrail_but_below_saturation():
    """Guardrail must fire *before* PoolSaturationMiddleware's reject threshold.

    saturation 0.8 sits between DB_GUARDRAIL_THRESHOLD (0.75) and
    DB_POOL_SATURATION_THRESHOLD (0.9) — guardrail fires, 530s do not.
    """
    metrics._gauges.clear()  # type: ignore[attr-defined]
    fake_app = MagicMock()
    fake_app.control.inspect.return_value.active_queues.return_value = {}
    with patch.object(cg.pool_health, "get_stats", return_value={
        "pool_size": 10, "active": 8, "idle": 2, "overflow": 0,
        "max_overflow": 20, "saturation": 0.8,
    }), patch.object(cg.settings, "DB_GUARDRAIL_THRESHOLD", 0.75), \
         patch.object(cg.settings, "DB_POOL_SATURATION_THRESHOLD", 0.9):
        result = cg.evaluate_guardrails(fake_app, log_alerts=False)
    assert result["alerts_active"] is True
    assert result["thresholds"]["db_guardrail"] == pytest.approx(0.75, abs=1e-6)
    assert result["thresholds"]["db_saturation_reject"] == pytest.approx(0.9, abs=1e-6)
    assert metrics._gauges["guardrail.alert.db"] == 1.0  # type: ignore[attr-defined]


def test_evaluate_guardrails_emits_log_warning_on_db_alert(caplog):
    metrics._gauges.clear()  # type: ignore[attr-defined]
    fake_app = MagicMock()
    fake_app.control.inspect.return_value.active_queues.return_value = {}
    with patch.object(cg.pool_health, "get_stats", return_value={
        "pool_size": 10, "active": 9, "idle": 1, "overflow": 0,
        "max_overflow": 20, "saturation": 0.95,
    }), patch.object(cg.settings, "DB_POOL_SATURATION_THRESHOLD", 0.9), \
         patch.object(cg.settings, "DB_GUARDRAIL_THRESHOLD", 0.75):
        with caplog.at_level(logging.WARNING, logger=cg.logger.name):
            result = cg.evaluate_guardrails(fake_app)
    assert result["alerts_active"] is True
    assert metrics._gauges["guardrail.alert.db"] == 1.0  # type: ignore[attr-defined]
    assert any(
        "[GUARDRAIL ALERT]" in rec.message and "DB pool" in rec.message
        for rec in caplog.records
    )


def test_guardrails_dict_overlay_round_trip():
    fake_app = MagicMock()
    fake_app.control.inspect.return_value.active_queues.return_value = {
        "w1": [{"name": "celery"}],
    }
    with patch.object(cg.settings, "APP_ENV", "staging"), \
         patch.object(cg.settings, "CELERY_WORKER_CONCURRENCY", None), \
         patch.object(cg.pool_health, "get_stats", return_value={
             "pool_size": 10, "active": 0, "idle": 10, "overflow": 0,
             "max_overflow": 20, "saturation": 0.0,
         }), patch.object(cg.settings, "DB_GUARDRAIL_THRESHOLD", 0.75), \
         patch.object(cg.settings, "DB_POOL_SATURATION_THRESHOLD", 0.9):
        payload = cg.guardrails_dict(fake_app)
    assert payload["env"] == "staging"
    assert payload["profile"]["concurrency"] == 4  # staging default
    assert payload["overrides_in_effect"]["concurrency_override"] is False
    assert payload["live_metrics"]["alerts_active"] is False


def test_guardrails_dict_reflects_concurrency_override():
    with patch.object(cg.settings, "APP_ENV", "prod"), \
         patch.object(cg.settings, "CELERY_WORKER_CONCURRENCY", 16), \
         patch.object(cg.pool_health, "get_stats", return_value={
             "pool_size": 20, "active": 0, "idle": 20, "overflow": 0,
             "max_overflow": 40, "saturation": 0.0,
         }), patch.object(cg.settings, "DB_GUARDRAIL_THRESHOLD", 0.75), \
         patch.object(cg.settings, "DB_POOL_SATURATION_THRESHOLD", 0.9):
        payload = cg.guardrails_dict(None)
    assert payload["profile"]["concurrency"] == 16
    assert payload["overrides_in_effect"]["concurrency_override"] is True
    assert payload["profile_defaults"]["concurrency"] == 8  # prod baseline preserved
