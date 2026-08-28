from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from app.core.config import settings
from app.utils.cache import TTLCache

logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class FallbackResult:
    data: Any
    source: str
    latency_ms: float
    stale: bool = False


@dataclass
class CircuitBreaker:
    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    last_failure_time: float = 0.0
    threshold: int = 0
    cooldown_seconds: int = 0

    def __post_init__(self) -> None:
        self.threshold = settings.BRIDGE_CIRCUIT_BREAKER_THRESHOLD
        self.cooldown_seconds = settings.BRIDGE_CIRCUIT_BREAKER_COOLDOWN_SECONDS

    def record_success(self) -> None:
        self.failure_count = 0
        self.state = CircuitState.CLOSED

    def record_failure(self) -> None:
        self.failure_count += 1
        self.last_failure_time = time.monotonic()
        if self.failure_count >= self.threshold:
            self.state = CircuitState.OPEN
            logger.warning(
                "Circuit breaker OPEN | failures=%d threshold=%d",
                self.failure_count,
                self.threshold,
            )

    def allow_request(self) -> bool:
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN:
            elapsed = time.monotonic() - self.last_failure_time
            if elapsed >= self.cooldown_seconds:
                self.state = CircuitState.HALF_OPEN
                return True
            return False
        # HALF_OPEN: allow a single probe request
        return True


class BridgeFallbackService:
    """Execute contract methods through a fallback chain with circuit-breaking.

    Fallback order: primary RPC -> secondary RPC -> local adapter -> cached response.
    Each upstream has its own circuit breaker to avoid hammering a failing endpoint.
    """

    def __init__(self) -> None:
        self._cache = TTLCache(ttl_seconds=settings.WALLET_CACHE_TTL_SECONDS)
        self._breakers: Dict[str, CircuitBreaker] = {}

    def _get_breaker(self, name: str) -> CircuitBreaker:
        if name not in self._breakers:
            self._breakers[name] = CircuitBreaker()
        return self._breakers[name]

    def execute_with_fallback(
        self,
        method: str,
        params: Dict[str, Any],
        upstreams: Optional[List[Callable[..., Any]]] = None,
    ) -> FallbackResult:
        if not settings.BRIDGE_FALLBACK_ENABLED:
            raise RuntimeError("Bridge fallback is disabled")

        callables = upstreams or []
        last_error: Optional[Exception] = None

        for idx, fn in enumerate(callables):
            name = f"{method}:upstream_{idx}"
            breaker = self._get_breaker(name)
            if not breaker.allow_request():
                logger.info("Skipping upstream (circuit open) | name=%s", name)
                continue
            start = time.monotonic()
            try:
                result = fn(method, **params)
                elapsed = (time.monotonic() - start) * 1000
                breaker.record_success()
                self._cache.set(f"{method}:{params}", result)
                logger.info(
                    "Fallback upstream succeeded | name=%s latency_ms=%.1f",
                    name,
                    elapsed,
                )
                return FallbackResult(data=result, source=name, latency_ms=elapsed)
            except Exception as exc:
                elapsed = (time.monotonic() - start) * 1000
                breaker.record_failure()
                last_error = exc
                logger.warning(
                    "Fallback upstream failed | name=%s latency_ms=%.1f reason=%s",
                    name,
                    elapsed,
                    exc,
                )

        # Last resort: serve stale cache
        cached = self._cache.get_with_meta(f"{method}:{params}")
        if cached is not None:
            logger.warning(
                "Serving stale cached response | method=%s age=%.0fs",
                method,
                cached.age_seconds,
            )
            return FallbackResult(
                data=cached.value,
                source="stale_cache",
                latency_ms=0.0,
                stale=True,
            )

        raise RuntimeError(
            f"All fallback upstreams exhausted and no cached data available for {method}"
        )
