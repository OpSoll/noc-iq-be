import time
import logging
import threading
from collections import OrderedDict
from typing import Optional

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
