"""Lightweight in-process TTL cache for dashboard aggregations.

Usage:
    cache = TTLCache(ttl_seconds=30)
    result = cache.get("key")
    cache.set("key", value)
    cache.invalidate("key")   # call after writes that affect cached data
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from threading import Lock
from typing import Any, Optional


# Explicit invalidation policy for this module.
# 1. TTL-based expiry: every entry expires after `ttl_seconds`.
# 2. Write-through invalidation: callers must call `invalidate` or
#    `invalidate_prefix` after any mutation that changes cached state.
# 3. Stale-data access: `get_with_meta` returns expired entries instead
#    of evicting them, enabling stale-fallback reads when live fetch fails.
CACHE_INVALIDATION_POLICY: str = (
    "TTL-based expiry (configurable) with explicit write-through invalidation. "
    "Expired entries are retained for stale-fallback reads via get_with_meta."
)


@dataclass
class CacheResult:
    """Metadata-rich cache lookup result returned by `get_with_meta`.

    Unlike `get`, stale entries are NOT evicted -- the caller decides
    whether to serve the value or re-fetch from the source.
    """

    value: Any
    age_seconds: float      # wall-clock age since the entry was stored
    ttl_remaining: float    # 0.0 when expired

    @property
    def is_expired(self) -> bool:
        return self.ttl_remaining <= 0.0


class TTLCache:
    """Thread-safe in-memory cache with per-entry TTL.

    Staleness behaviour: entries expire after `ttl_seconds`. Callers that
    need fresh data after a write should call `invalidate` explicitly.
    An empty or recently-changed dataset is handled correctly because the
    cache is bypassed on a miss and always stores the latest computed value.
    """

    def __init__(self, ttl_seconds: int = 30) -> None:
        self._ttl = ttl_seconds
        self._store: dict[str, tuple[Any, float]] = {}
        self._lock = Lock()

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            value, expires_at = entry
            if time.monotonic() > expires_at:
                del self._store[key]
                return None
            return value

    def get_with_meta(self, key: str) -> 'CacheResult | None':
        """Return the cache entry and its metadata even when the TTL has expired.

        Unlike `get`, this method does NOT evict the expired entry.  Use it
        when you need stale data as a fallback after a failed live fetch.
        Returns None only when the key was never set or was explicitly
        invalidated.
        """
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            value, expires_at = entry
            now = time.monotonic()
            age = now - (expires_at - self._ttl)
            ttl_remaining = max(0.0, expires_at - now)
            return CacheResult(value=value, age_seconds=age, ttl_remaining=ttl_remaining)

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._store[key] = (value, time.monotonic() + self._ttl)

    def invalidate(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)

    def invalidate_prefix(self, prefix: str) -> None:
        with self._lock:
            keys = [k for k in self._store if k.startswith(prefix)]
            for k in keys:
                del self._store[k]
