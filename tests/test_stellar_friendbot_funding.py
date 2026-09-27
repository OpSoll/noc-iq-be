"""Tests for the Stellar testnet Friendbot auto-faucet funding service.

Acceptance criteria covered:
  * ``request_friendbot_funding(address)`` service method.
  * Friendbot request auto-triggered when the testnet balance is 0 XLM.
  * The Friendbot response is logged.

Every test runs offline — httpx is stubbed, no Horizon or faucet is called.
"""
from __future__ import annotations

import asyncio
import logging
from decimal import Decimal
from unittest.mock import patch

import pytest

from app.core.config import Settings
from app.services.stellar.friendbot import FriendbotError, FriendbotService

VALID_ADDRESS = "GA" + "B" * 54
FUNDED_RESPONSE = {
    "hash": "a" * 64,
    "ledger": 123456,
    "successful": True,
}
ALREADY_EXISTS_RESPONSE = {
    "status": 400,
    "title": "Transaction Failed",
    "extras": {"result_codes": {"transaction": "tx_failed", "operations": ["op_already_exists"]}},
}


class FakeResponse:
    """Minimal httpx.Response stand-in."""

    def __init__(self, status_code: int, json_body=None, text: str = ""):
        self.status_code = status_code
        self._json = json_body
        self.text = text

    def json(self):
        if self._json is None:
            raise ValueError("no json body")
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
    """Async context manager returning a canned response and recording calls."""

    calls: list[tuple[str, dict]] = []

    def __init__(self, response=None, error=None, **kwargs):
        self._response = response
        self._error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def get(self, url, params=None):
        type(self).calls.append((url, params or {}))
        if self._error is not None:
            raise self._error
        return self._response


def _client_factory(response=None, error=None):
    """Return a drop-in replacement for ``httpx.AsyncClient``."""

    def factory(*args, **kwargs):
        return FakeAsyncClient(response=response, error=error)

    return factory


def _service(**overrides) -> FriendbotService:
    settings = Settings(**overrides)
    return FriendbotService(settings=settings)


def run(coro):
    """Drive a coroutine to completion without needing a pytest async plugin."""
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _reset_calls():
    FakeAsyncClient.calls = []
    yield
    FakeAsyncClient.calls = []


# --------------------------------------------------------------------------- #
# request_friendbot_funding                                                    #
# --------------------------------------------------------------------------- #

def test_request_friendbot_funding_returns_funded_result():
    service = _service(STELLAR_NETWORK="testnet")
    with patch(
        "app.services.stellar.friendbot.httpx.AsyncClient",
        _client_factory(FakeResponse(200, FUNDED_RESPONSE)),
    ):
        result = run(service.request_friendbot_funding(VALID_ADDRESS))

    assert result.funded is True
    assert result.outcome == "funded"
    assert result.transaction_hash == FUNDED_RESPONSE["hash"]
    assert result.ledger == 123456
    assert result.status_code == 200


def test_request_friendbot_funding_calls_faucet_with_address():
    service = _service(STELLAR_NETWORK="testnet")
    with patch(
        "app.services.stellar.friendbot.httpx.AsyncClient",
        _client_factory(FakeResponse(200, FUNDED_RESPONSE)),
    ):
        run(service.request_friendbot_funding(VALID_ADDRESS))

    url, params = FakeAsyncClient.calls[-1]
    assert url == "https://friendbot.stellar.org"
    assert params == {"addr": VALID_ADDRESS}


def test_already_existing_account_is_not_an_error():
    service = _service(STELLAR_NETWORK="testnet")
    with patch(
        "app.services.stellar.friendbot.httpx.AsyncClient",
        _client_factory(FakeResponse(400, ALREADY_EXISTS_RESPONSE)),
    ):
        result = run(service.request_friendbot_funding(VALID_ADDRESS))

    assert result.outcome == "already_funded"
    assert result.funded is False


def test_friendbot_failure_raises_retryable_error():
    import httpx

    service = _service(STELLAR_NETWORK="testnet")
    with patch(
        "app.services.stellar.friendbot.httpx.AsyncClient",
        _client_factory(error=httpx.ConnectError("boom")),
    ):
        with pytest.raises(FriendbotError) as exc:
            run(service.request_friendbot_funding(VALID_ADDRESS))

    assert exc.value.reason == FriendbotError.REASON_REQUEST_FAILED
    assert exc.value.retryable is True


def test_friendbot_server_error_raises():
    service = _service(STELLAR_NETWORK="testnet")
    with patch(
        "app.services.stellar.friendbot.httpx.AsyncClient",
        _client_factory(FakeResponse(500, {"detail": "faucet down"})),
    ):
        with pytest.raises(FriendbotError) as exc:
            run(service.request_friendbot_funding(VALID_ADDRESS))

    assert exc.value.reason == FriendbotError.REASON_REQUEST_FAILED


def test_mainnet_is_refused():
    service = _service(STELLAR_NETWORK="mainnet")
    with pytest.raises(FriendbotError) as exc:
        run(service.request_friendbot_funding(VALID_ADDRESS))

    assert exc.value.reason == FriendbotError.REASON_UNSUPPORTED_NETWORK
    assert FakeAsyncClient.calls == []


def test_disabled_flag_is_refused():
    service = _service(STELLAR_NETWORK="testnet", STELLAR_FRIENDBOT_ENABLED=False)
    with pytest.raises(FriendbotError) as exc:
        run(service.request_friendbot_funding(VALID_ADDRESS))

    assert exc.value.reason == FriendbotError.REASON_DISABLED


def test_malformed_address_is_rejected_before_any_request():
    service = _service(STELLAR_NETWORK="testnet")
    with pytest.raises(FriendbotError) as exc:
        run(service.request_friendbot_funding("not-a-stellar-address"))

    assert exc.value.reason == FriendbotError.REASON_INVALID_ADDRESS
    assert FakeAsyncClient.calls == []


# --------------------------------------------------------------------------- #
# Auto-trigger on zero balance                                                 #
# --------------------------------------------------------------------------- #

def test_zero_balance_auto_triggers_friendbot():
    service = _service(STELLAR_NETWORK="testnet")
    with (
        patch.object(service, "get_native_balance", return_value=Decimal("0")),
        patch(
            "app.services.stellar.friendbot.httpx.AsyncClient",
            _client_factory(FakeResponse(200, FUNDED_RESPONSE)),
        ),
    ):
        result = run(service.fund_if_unfunded(VALID_ADDRESS))

    assert result.funded is True
    assert result.outcome == "funded"
    assert result.balance_before == Decimal("0")


def test_positive_balance_skips_funding():
    service = _service(STELLAR_NETWORK="testnet")
    with patch.object(service, "get_native_balance", return_value=Decimal("12.5")):
        result = run(service.fund_if_unfunded(VALID_ADDRESS))

    assert result.outcome == "skipped"
    assert result.funded is False
    assert FakeAsyncClient.calls == []


def test_missing_account_reads_as_zero_balance():
    """A testnet reset leaves a 404 on Horizon — treated as 0 XLM."""
    service = _service(STELLAR_NETWORK="testnet")
    with patch(
        "app.services.stellar.friendbot.httpx.AsyncClient",
        _client_factory(FakeResponse(404, {"title": "Resource Missing"})),
    ):
        balance = run(service.get_native_balance(VALID_ADDRESS))

    assert balance == Decimal("0")


def test_native_balance_parsed_from_horizon():
    body = {
        "balances": [
            {"asset_type": "credit_alphanum4", "asset_code": "USDC", "balance": "700.0"},
            {"asset_type": "native", "balance": "101.5000000"},
        ]
    }
    service = _service(STELLAR_NETWORK="testnet")
    with patch(
        "app.services.stellar.friendbot.httpx.AsyncClient",
        _client_factory(FakeResponse(200, body)),
    ):
        balance = run(service.get_native_balance(VALID_ADDRESS))

    assert balance == Decimal("101.5000000")


# --------------------------------------------------------------------------- #
# Response logging                                                             #
# --------------------------------------------------------------------------- #

def test_friendbot_response_is_logged(caplog):
    service = _service(STELLAR_NETWORK="testnet")
    with (
        caplog.at_level(logging.INFO, logger="app.services.stellar.friendbot"),
        patch(
            "app.services.stellar.friendbot.httpx.AsyncClient",
            _client_factory(FakeResponse(200, FUNDED_RESPONSE)),
        ),
    ):
        run(service.request_friendbot_funding(VALID_ADDRESS))

    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "Friendbot funding response" in logged
    assert FUNDED_RESPONSE["hash"] in logged


def test_failed_friendbot_response_is_logged_at_error(caplog):
    service = _service(STELLAR_NETWORK="testnet")
    with (
        caplog.at_level(logging.ERROR, logger="app.services.stellar.friendbot"),
        patch(
            "app.services.stellar.friendbot.httpx.AsyncClient",
            _client_factory(FakeResponse(503, {"detail": "faucet unavailable"})),
        ),
    ):
        with pytest.raises(FriendbotError):
            run(service.request_friendbot_funding(VALID_ADDRESS))

    assert any(r.levelno >= logging.ERROR for r in caplog.records)
    assert "Friendbot funding failed" in "\n".join(r.getMessage() for r in caplog.records)
