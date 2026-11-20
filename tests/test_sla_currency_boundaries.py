"""Boundary tests for the XLM currency conversion helper (#903)."""

from decimal import Decimal
from unittest.mock import Mock, patch

import httpx
import pytest

from app.services.sla.currency import ExchangeRateUnavailableError, XLMCurrencyConverter

URL = "https://feed.test"


def feed(usd, eur=None):
    response = Mock()
    response.json.return_value = {"stellar": {"usd": usd, "eur": usd if eur is None else eur}}
    return response


def test_missing_rate_raises():
    converter = XLMCurrencyConverter(URL)
    response = Mock()
    response.json.return_value = {"stellar": {}}

    with patch("app.services.sla.currency.httpx.get", return_value=response):
        with pytest.raises(ExchangeRateUnavailableError):
            converter.get_xlm_usd_rate()


def test_non_positive_rate_raises():
    converter = XLMCurrencyConverter(URL)

    with patch("app.services.sla.currency.httpx.get", return_value=feed(0)):
        with pytest.raises(ExchangeRateUnavailableError, match="non-positive"):
            converter.convert_xlm(10, "USD")


def test_network_failure_raises():
    converter = XLMCurrencyConverter(URL)

    with patch(
        "app.services.sla.currency.httpx.get",
        side_effect=httpx.ConnectError("offline"),
    ):
        with pytest.raises(ExchangeRateUnavailableError):
            converter.get_xlm_usd_rate()


def test_zero_amount_converts_to_zero():
    converter = XLMCurrencyConverter(URL)

    with patch("app.services.sla.currency.httpx.get", return_value=feed(0.5)):
        assert converter.convert_xlm(0, "USD") == Decimal("0")


def test_conversion_avoids_float_rounding_drift():
    converter = XLMCurrencyConverter(URL)

    with patch("app.services.sla.currency.httpx.get", return_value=feed(0.3)):
        # 0.1 * 0.3 is 0.030000000000000004 in binary floating point.
        assert converter.convert_xlm(0.1, "USD") == Decimal("0.03")


def test_decimal_amounts_keep_precision():
    converter = XLMCurrencyConverter(URL)

    with patch("app.services.sla.currency.httpx.get", return_value=feed("1.2345678")):
        assert converter.convert_xlm(Decimal("2"), "USD") == Decimal("2.4691356")
