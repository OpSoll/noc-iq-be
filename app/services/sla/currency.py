"""Currency conversion for XLM-denominated SLA penalties."""
from __future__ import annotations

from decimal import Decimal

import httpx

from app.utils.cache import TTLCache


class ExchangeRateUnavailableError(RuntimeError):
    """Raised when a live XLM exchange rate cannot be obtained."""


class XLMCurrencyConverter:
    """Fetch and cache XLM/USD rates, then convert values to USD or EUR."""

    def __init__(
        self,
        price_feed_url: str,
        cache: TTLCache | None = None,
    ) -> None:
        self.price_feed_url = price_feed_url
        self._cache = cache or TTLCache(ttl_seconds=15 * 60)

    def _get_xlm_rate(self, currency: str) -> Decimal:
        cache_key = f"xlm_{currency.lower()}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        try:
            response = httpx.get(self.price_feed_url, timeout=5.0)
            response.raise_for_status()
            rates = response.json()["stellar"]
            for target in ("usd", "eur"):
                self._cache.set(f"xlm_{target}", Decimal(str(rates[target])))
            rate = self._cache.get(cache_key)
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise ExchangeRateUnavailableError("Unable to fetch live XLM exchange rates.") from exc
        if rate is None or rate <= 0:
            raise ExchangeRateUnavailableError(f"Price feed returned a non-positive XLM/{currency} rate.")
        return rate

    def get_xlm_usd_rate(self) -> Decimal:
        return self._get_xlm_rate("USD")

    def convert_xlm(self, amount_xlm: Decimal | str | float | int, currency: str) -> Decimal:
        target = currency.upper()
        if target not in {"USD", "EUR"}:
            raise ValueError("currency must be USD or EUR")
        return Decimal(str(amount_xlm)) * self._get_xlm_rate(target)