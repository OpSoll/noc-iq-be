from __future__ import annotations

import logging
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class CacheKeyNamespace(str, Enum):
    WALLET = "wallet"
    RATE_LIMIT = "rate-limit"
    SESSION = "session"
    OUTAGE = "outage"
    METRICS = "metrics"


_NAMESPACE_TTL_LIMITS: dict[CacheKeyNamespace, int] = {
    CacheKeyNamespace.WALLET: 300,
    CacheKeyNamespace.RATE_LIMIT: 60,
    CacheKeyNamespace.SESSION: 3600,
    CacheKeyNamespace.OUTAGE: 120,
    CacheKeyNamespace.METRICS: 30,
}


class CacheGovernance:
    """Validates cache keys against namespace rules and enforces TTL limits."""

    @staticmethod
    def validate_namespace(namespace: CacheKeyNamespace) -> None:
        if not isinstance(namespace, CacheKeyNamespace):
            raise ValueError(f"Unknown cache namespace: {namespace}")

    @staticmethod
    def max_ttl(namespace: CacheKeyNamespace) -> int:
        CacheGovernance.validate_namespace(namespace)
        return _NAMESPACE_TTL_LIMITS[namespace]

    @staticmethod
    def enforce_ttl(namespace: CacheKeyNamespace, requested_ttl: int) -> int:
        """Return the effective TTL, clamped to the policy maximum."""
        CacheGovernance.validate_namespace(namespace)
        max_ttl = _NAMESPACE_TTL_LIMITS[namespace]
        if requested_ttl > max_ttl:
            logger.warning(
                "TTL %ds exceeds policy max %ds for namespace '%s'; clamping",
                requested_ttl,
                max_ttl,
                namespace.value,
            )
            return max_ttl
        return requested_ttl

    @staticmethod
    def build_key(namespace: CacheKeyNamespace, *parts: str) -> str:
        return CacheKeyBuilder.build(namespace, *parts)


class CacheKeyBuilder:
    """Utility that constructs properly namespaced cache keys."""

    @staticmethod
    def build(namespace: CacheKeyNamespace, *parts: str) -> str:
        CacheGovernance.validate_namespace(namespace)
        joined = ":".join(parts)
        return f"{namespace.value}:{joined}" if joined else namespace.value

    @staticmethod
    def parse(key: str) -> tuple[Optional[CacheKeyNamespace], str]:
        """Return (namespace, rest) or (None, key) if prefix is unknown."""
        for ns in CacheKeyNamespace:
            prefix = ns.value + ":"
            if key.startswith(prefix):
                return ns, key[len(prefix):]
        return None, key
