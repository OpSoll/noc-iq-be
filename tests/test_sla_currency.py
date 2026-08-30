from decimal import Decimal
from unittest.mock import Mock, patch

import pytest

from app.services.sla.currency import ExchangeRateUnavailableError, XLMCurrencyConverter


def test_converts_xlm_to_usd_and_eur_and_caches_rate():
    response = Mock()
    response.json.return_value = {"stellar": {"usd": 0.1, "eur": 0.09}}
    converter = XLMCurrencyConverter("https://feed.test")

    with patch("app.services.sla.currency.httpx.get", return_value=response) as get:
        assert converter.convert_xlm("20", "USD") == Decimal("2.0")
        assert converter.convert_xlm("20", "EUR") == Decimal("1.80")

    get.assert_called_once_with("https://feed.test", timeout=5.0)


def test_rejects_bad_feed_response():
    converter = XLMCurrencyConverter("https://feed.test")
    response = Mock()
    response.json.return_value = {}

    with patch("app.services.sla.currency.httpx.get", return_value=response):
        with pytest.raises(ExchangeRateUnavailableError):
            converter.get_xlm_usd_rate()


def test_rejects_unsupported_currency():
    converter = XLMCurrencyConverter("https://feed.test")
    with pytest.raises(ValueError, match="USD or EUR"):
        converter.convert_xlm(1, "GBP")