from __future__ import annotations

import logging
import time
from typing import Optional

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.core.cache_governance import CacheGovernance, CacheKeyNamespace

logger = logging.getLogger(__name__)

# In-memory rate limiter as a lightweight default.
# Swap the store for Redis-backed implementation in production.


class _TokenBucket:
    """Simple token-bucket stored in-process."""

    def __init__(self, capacity: int, refill_rate: float) -> None:
        self.capacity = capacity
        self.tokens = float(capacity)
        self.refill_rate = refill_rate  # tokens per second
        self.last_refill = time.monotonic()

    def consume(self) -> bool:
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
        self.last_refill = now
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False


_buckets: dict[str, _TokenBucket] = {}
_bucket_lock = __import__("threading").Lock()


def _get_bucket(key: str, capacity: int, refill_rate: float) -> _TokenBucket:
    with _bucket_lock:
        if key not in _buckets:
            _buckets[key] = _TokenBucket(capacity, refill_rate)
        return _buckets[key]


class RateLimiterMiddleware(BaseHTTPMiddleware):
    """Per-IP token-bucket rate limiter."""

    def __init__(self, app, capacity: int = 60, refill_rate: float = 1.0) -> None:
        super().__init__(app)
        self.capacity = capacity
        self.refill_rate = refill_rate

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        client_ip = request.client.host if request.client else "unknown"
        rate_key = CacheGovernance.build_key(CacheKeyNamespace.RATE_LIMIT, client_ip)

        bucket = _get_bucket(rate_key, self.capacity, self.refill_rate)
        if not bucket.consume():
            logger.warning("Rate limit exceeded for %s", client_ip)
            return Response(
                content='{"detail":"Too Many Requests"}',
                status_code=429,
                media_type="application/json",
            )

        return await call_next(request)
