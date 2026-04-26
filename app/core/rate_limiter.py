"""
Simple rate limiter for auth endpoints.
In production, this should be replaced with Redis-based rate limiting.
"""
from collections import defaultdict
from time import time
from typing import Dict, List
from app.core.config import settings


class SimpleRateLimiter:
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


# Global rate limiter instance
rate_limiter = SimpleRateLimiter()