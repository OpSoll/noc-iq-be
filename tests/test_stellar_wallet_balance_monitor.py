"""Tests for the Stellar wallet XLM/USDC balance threshold monitor.

Acceptance criteria covered:
  * The operator wallet balance is queried every 15 minutes (beat schedule).
  * An alert is triggered when XLM < 50 or USDC < $500.
  * Wallet health metrics are exposed on ``/api/v1/health/detailed``.
"""
from __future__ import annotations

import asyncio
import logging
from decimal import Decimal
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.services.stellar.balance_monitor import (
    STATUS_LOW,
    STATUS_OK,
    STATUS_UNKNOWN,
    WalletBalanceMonitor,
)

ADDRESS = "GA" + "C" * 54
ISSUER = "GB" + "D" * 54


def run(coro):
    """Drive a coroutine to completion without needing a pytest async plugin."""
    return asyncio.run(coro)


def _monitor(**overrides) -> WalletBalanceMonitor:
    settings = Settings(
        WALLET_MONITOR_ADDRESS=ADDRESS,
        PAYMENT_ASSET_CODE="USDC",
        PAYMENT_ASSET_ISSUER=ISSUER,
        **overrides,
    )
    return WalletBalanceMonitor(settings=settings)


def _horizon_balances(xlm: str, usdc: str | None) -> dict:
    balances = [{"asset_type": "native", "balance": xlm}]
    if usdc is not None:
        balances.append(
            {
                "asset_type": "credit_alphanum4",
                "asset_code": "USDC",
                "asset_issuer": ISSUER,
                "balance": usdc,
            }
        )
    return {"balances": balances}


class FakeResponse:
    def __init__(self, status_code: int, json_body=None):
        self.status_code = status_code
        self._json = json_body

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=httpx.Request("GET", "https://horizon.test"),
                response=httpx.Response(self.status_code),
            )


class FakeAsyncClient:
    def __init__(self, response=None, error=None):
        self._response = response
        self._error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def get(self, url, params=None):
        if self._error is not None:
            raise self._error
        return self._response


def _client_factory(response=None, error=None):
    def factory(*args, **kwargs):
        return FakeAsyncClient(response=response, error=error)

    return factory


def _patch_horizon(response=None, error=None):
    return patch(
        "app.services.stellar.balance_monitor.httpx.AsyncClient",
        _client_factory(response=response, error=error),
    )


# --------------------------------------------------------------------------- #
# Balance reads                                                                #
# --------------------------------------------------------------------------- #

def test_balances_are_parsed_from_horizon():
    monitor = _monitor()
    with _patch_horizon(FakeResponse(200, _horizon_balances("120.5", "900.0"))):
        balances = run(monitor.fetch_balances(ADDRESS))

    assert balances["XLM"] == Decimal("120.5")
    assert balances["USDC"] == Decimal("900.0")


def test_asset_without_a_trustline_reads_as_zero():
    monitor = _monitor()
    with _patch_horizon(FakeResponse(200, _horizon_balances("120.5", None))):
        balances = run(monitor.fetch_balances(ADDRESS))

    assert balances["USDC"] == Decimal("0")


def test_missing_account_reads_as_zero_balances():
    monitor = _monitor()
    with _patch_horizon(FakeResponse(404, {"title": "Resource Missing"})):
        balances = run(monitor.fetch_balances(ADDRESS))

    assert balances == {"XLM": Decimal("0"), "USDC": Decimal("0")}


# --------------------------------------------------------------------------- #
# Threshold evaluation                                                         #
# --------------------------------------------------------------------------- #

def test_healthy_balances_report_ok():
    monitor = _monitor()
    with _patch_horizon(FakeResponse(200, _horizon_balances("120.0", "900.0"))):
        health = run(monitor.check())

    assert health.status == STATUS_OK
    assert health.healthy is True
    assert health.breaches == []


def test_low_xlm_balance_is_flagged():
    monitor = _monitor()
    with _patch_horizon(FakeResponse(200, _horizon_balances("49.9999999", "900.0"))):
        health = run(monitor.check())

    assert health.status == STATUS_LOW
    assert [b.asset_code for b in health.breaches] == ["XLM"]
    assert health.breaches[0].threshold == Decimal("50.0")


def test_low_usdc_balance_is_flagged():
    monitor = _monitor()
    with _patch_horizon(FakeResponse(200, _horizon_balances("120.0", "499.99"))):
        health = run(monitor.check())

    assert health.status == STATUS_LOW
    assert [b.asset_code for b in health.breaches] == ["USDC"]
    assert health.breaches[0].shortfall == Decimal("0.01")


def test_balances_exactly_at_the_threshold_are_healthy():
    monitor = _monitor()
    with _patch_horizon(FakeResponse(200, _horizon_balances("50", "500"))):
        health = run(monitor.check())

    assert health.status == STATUS_OK


def test_both_assets_below_threshold_produce_two_breaches():
    monitor = _monitor()
    with _patch_horizon(FakeResponse(200, _horizon_balances("1.0", "10.0"))):
        health = run(monitor.check())

    assert {b.asset_code for b in health.breaches} == {"XLM", "USDC"}


def test_thresholds_are_configurable():
    monitor = _monitor(WALLET_MIN_XLM_BALANCE=200.0)
    with _patch_horizon(FakeResponse(200, _horizon_balances("120.0", "900.0"))):
        health = run(monitor.check())

    assert health.status == STATUS_LOW
    assert health.breaches[0].threshold == Decimal("200.0")


def test_horizon_failure_reports_unknown_rather_than_ok():
    import httpx

    monitor = _monitor()
    with _patch_horizon(error=httpx.ConnectError("horizon down")):
        health = run(monitor.check())

    assert health.status == STATUS_UNKNOWN
    assert health.healthy is None or health.healthy is False
    assert health.error is not None


def test_unconfigured_address_reports_unknown():
    settings = Settings(WALLET_MONITOR_ADDRESS="", PAYMENT_FROM_ADDRESS="   ")
    monitor = WalletBalanceMonitor(settings=settings)

    health = run(monitor.check())

    assert health.status == STATUS_UNKNOWN
    assert "address" in (health.error or "").lower()


# --------------------------------------------------------------------------- #
# Alert trigger                                                                #
# --------------------------------------------------------------------------- #

def test_alert_is_triggered_and_logged_on_breach(caplog):
    monitor = _monitor()
    with _patch_horizon(FakeResponse(200, _horizon_balances("10.0", "900.0"))):
        health = run(monitor.check())

    with caplog.at_level(logging.ERROR, logger="app.services.stellar.balance_monitor"):
        triggered = monitor.trigger_alert(health)

    assert triggered is True
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "below operational threshold" in logged
    assert "XLM" in logged


def test_no_alert_when_balances_are_healthy(caplog):
    monitor = _monitor()
    with _patch_horizon(FakeResponse(200, _horizon_balances("120.0", "900.0"))):
        health = run(monitor.check())

    with caplog.at_level(logging.ERROR, logger="app.services.stellar.balance_monitor"):
        triggered = monitor.trigger_alert(health)

    assert triggered is False
    assert caplog.records == []


def test_alert_webhook_is_posted_when_configured():
    monitor = _monitor(WALLET_ALERT_WEBHOOK_URL="https://alerts.example/hook")
    with _patch_horizon(FakeResponse(200, _horizon_balances("10.0", "10.0"))):
        health = run(monitor.check())

    with patch("app.services.stellar.balance_monitor.httpx.post") as post:
        monitor.trigger_alert(health)

    assert post.call_count == 1
    payload = post.call_args.kwargs["json"]
    assert payload["alert"] == "stellar_wallet_balance_low"
    assert payload["status"] == STATUS_LOW
    assert len(payload["breaches"]) == 2


def test_alert_webhook_failure_does_not_propagate():
    import httpx

    monitor = _monitor(WALLET_ALERT_WEBHOOK_URL="https://alerts.example/hook")
    with _patch_horizon(FakeResponse(200, _horizon_balances("10.0", "10.0"))):
        health = run(monitor.check())

    with patch(
        "app.services.stellar.balance_monitor.httpx.post",
        side_effect=httpx.ConnectError("unreachable"),
    ):
        assert monitor.trigger_alert(health) is True


# --------------------------------------------------------------------------- #
# Cached health snapshot                                                       #
# --------------------------------------------------------------------------- #

def test_run_check_alerts_and_stores_the_snapshot():
    monitor = _monitor()
    with (
        _patch_horizon(FakeResponse(200, _horizon_balances("10.0", "900.0"))),
        patch.object(monitor, "trigger_alert") as alert,
        patch.object(monitor, "store_health") as store,
    ):
        health = run(monitor.run_check())

    assert health.status == STATUS_LOW
    alert.assert_called_once()
    store.assert_called_once()


def test_read_health_falls_back_when_no_snapshot_exists():
    monitor = _monitor()
    with patch.object(monitor, "_get_redis", side_effect=RuntimeError("no redis")):
        payload = monitor.read_health()

    assert payload["status"] == STATUS_UNKNOWN
    assert payload["healthy"] is None
    assert payload["thresholds"]["XLM"] == "50.0"


def test_health_snapshot_is_json_serialisable():
    import json

    monitor = _monitor()
    with _patch_horizon(FakeResponse(200, _horizon_balances("10.0", "900.0"))):
        health = run(monitor.check())

    payload = json.loads(json.dumps(health.as_dict()))
    assert payload["balances"]["XLM"] == "10.0"
    assert payload["breaches"][0]["asset_code"] == "XLM"


# --------------------------------------------------------------------------- #
# Beat schedule + health endpoint exposure                                     #
# --------------------------------------------------------------------------- #

def test_balance_monitor_runs_every_15_minutes():
    from app.tasks.celery_app import celery_app

    entry = celery_app.conf.beat_schedule["monitor-wallet-balances"]

    assert entry["task"] == "app.tasks.payment_tasks.monitor_wallet_balances"
    assert entry["schedule"] == 900.0


def test_detailed_health_exposes_wallet_metrics():
    from app.main import app

    snapshot = {
        "address": ADDRESS,
        "status": STATUS_LOW,
        "healthy": False,
        "checked_at": "2026-08-28T00:00:00+00:00",
        "balances": {"XLM": "10.0", "USDC": "900.0"},
        "thresholds": {"XLM": "50.0", "USDC": "500.0"},
        "breaches": [
            {
                "asset_code": "XLM",
                "balance": "10.0",
                "threshold": "50.0",
                "shortfall": "40.0",
            }
        ],
        "error": None,
    }

    client = TestClient(app, raise_server_exceptions=False)
    with patch(
        "app.api.v1.endpoints.health.read_wallet_health", return_value=snapshot
    ):
        response = client.get("/api/v1/health/detailed")

    assert response.status_code in {200, 503}
    wallet = response.json()["wallet"]
    assert wallet["status"] == STATUS_LOW
    assert wallet["balances"]["XLM"] == "10.0"
    assert wallet["breaches"][0]["asset_code"] == "XLM"


def test_detailed_health_survives_an_unavailable_wallet_snapshot():
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)
    with patch(
        "app.api.v1.endpoints.health.read_wallet_health",
        side_effect=RuntimeError("redis down"),
    ):
        response = client.get("/api/v1/health/detailed")

    assert response.status_code in {200, 503}
    assert response.json()["wallet"]["status"] == STATUS_UNKNOWN


def test_monitor_task_is_a_noop_when_disabled():
    from app.tasks import payment_tasks

    with patch.object(payment_tasks.settings, "WALLET_BALANCE_MONITOR_ENABLED", False):
        result = payment_tasks.monitor_wallet_balances()

    assert result == {"status": "disabled"}
