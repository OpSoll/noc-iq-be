import time
import logging
import threading
from collections import OrderedDict, defaultdict
from typing import Dict, List, Optional

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


"""
Simple rate limiter for auth endpoints.
In production, this should be replaced with Redis-based rate limiting.
"""
from collections import defaultdict
from time import time
from typing import Dict, List
from app.core.config import settings

logger = logging.getLogger(__name__)


class _RedisBackend:
    def __init__(self, redis_url: str):
        import redis
        self._client = redis.Redis.from_url(redis_url, decode_responses=True)

    def is_available(self) -> bool:
        try:
            return self._client.ping()
        except Exception:
            return False

    def sliding_window_check(self, key: str, limit: int, window_seconds: int) -> bool:
        now = time.time()
        pipe = self._client.pipeline()
        pipe.zremrangebyscore(key, 0, now - window_seconds)
        pipe.zadd(key, {str(now): now})
        pipe.zcard(key)
        pipe.expire(key, window_seconds)
        results = pipe.execute()
        count = results[2]
        return count <= limit


class _MemoryBackend:
    def __init__(self):
        self._windows: dict[str, OrderedDict] = {}
        self._lock = threading.Lock()

    def is_available(self) -> bool:
        return True

    def sliding_window_check(self, key: str, limit: int, window_seconds: int) -> bool:
        now = time.time()
        with self._lock:
            if key not in self._windows:
                self._windows[key] = OrderedDict()
            window = self._windows[key]
            cutoff = now - window_seconds
            while window and next(iter(window)) < cutoff:
                window.popitem(last=False)
            window[str(now)] = now
            if len(window) > settings.RATE_LIMIT_MAX_KEYS:
                evict = settings.RATE_LIMIT_EVICT_BATCH_SIZE
                for _ in range(min(evict, len(window) - settings.RATE_LIMIT_MAX_KEYS // 2)):
                    window.popitem(last=False)
            return len(window) <= limit


class RateLimiter:
    def __init__(self):
        self._backend_name = settings.RATE_LIMIT_BACKEND
        self._redis_backend: Optional[_RedisBackend] = None
        self._memory_backend = _MemoryBackend()
        self._active_backend_name = self._backend_name
        self._stats = {"hits": 0, "misses": 0, "fallback_count": 0}
        self._switch_log_time: Optional[float] = None

        if self._backend_name == "redis":
            try:
                self._redis_backend = _RedisBackend(settings.REDIS_URL)
                if self._redis_backend.is_available():
                    self._active_backend_name = "redis"
                else:
                    self._active_backend_name = "memory"
                    logger.warning("Redis unavailable at startup, using memory backend")
            except Exception:
                self._active_backend_name = "memory"
                logger.warning("Redis connection failed, using memory backend")

    @property
    def _backend(self):
        if self._active_backend_name == "redis" and self._redis_backend:
            return self._redis_backend
        return self._memory_backend

    def _maybe_fallback(self):
        if self._active_backend_name == "redis" and self._redis_backend:
            if not self._redis_backend.is_available():
                self._active_backend_name = "memory"
                self._stats["fallback_count"] += 1
                self._switch_log_time = time.time()
                logger.warning("Redis unavailable, falling back to memory backend (count=%d)", self._stats["fallback_count"])

    def check(self, key: str, limit: int, window_seconds: int = 60) -> bool:
        self._maybe_fallback()
        allowed = self._backend.sliding_window_check(key, limit, window_seconds)
        if allowed:
            self._stats["hits"] += 1
        else:
            self._stats["misses"] += 1
        return allowed

    def get_metrics(self) -> dict:
        return {
            "backend": self._active_backend_name,
            "configured_backend": self._backend_name,
            "hits": self._stats["hits"],
            "misses": self._stats["misses"],
            "fallback_count": self._stats["fallback_count"],
            "last_switch": self._switch_log_time,
        }


rate_limiter = RateLimiter()


class SimpleRateLimiter:
    """Simple rate limiter for auth endpoints.
    In production, this should be replaced with Redis-based rate limiting.
    """

    def __init__(self):
        self.requests: Dict[str, List[float]] = defaultdict(list)

    def is_allowed(self, key: str) -> bool:
        """Check if the key is allowed based on rate limits."""
        now = time()
        window_start = now - settings.AUTH_RATE_LIMIT_WINDOW_SECONDS

        # Clean old requests
        self.requests[key] = [t for t in self.requests[key] if t > window_start]

        if len(self.requests[key]) >= settings.AUTH_RATE_LIMIT_REQUESTS:
            return False

        self.requests[key].append(now)
        return True


auth_rate_limiter = SimpleRateLimiter()
# Global rate limiter instance
rate_limiter = SimpleRateLimiter()
