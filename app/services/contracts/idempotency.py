from __future__ import annotations

import logging
import uuid
from typing import Optional

from redis import Redis

from app.core.config import settings

logger = logging.getLogger(__name__)

_KEY_PREFIX = "idempotency:"


class IdempotencyService:
    """Generate and verify idempotency tokens for contract calls.

    Tokens are stored in Redis with a configurable TTL so that duplicate
    contract invocations with the same token are detected and rejected.
    """

    def __init__(self, redis_client: Optional[Redis] = None) -> None:
        self._redis = redis_client or Redis.from_url(
            settings.CELERY_RESULT_BACKEND, decode_responses=True
        )
        self._ttl = settings.CONTRACT_IDEMPOTENCY_TTL_SECONDS

    def generate_token(self) -> str:
        return str(uuid.uuid4())

    def store_token(self, token: str, payload: str) -> bool:
        key = f"{_KEY_PREFIX}{token}"
        # SET NX ensures we only store once; returns False if key already exists.
        stored = self._redis.set(key, payload, ex=self._ttl, nx=True)
        if not stored:
            logger.warning("Duplicate idempotency token detected | token=%s", token)
        return bool(stored)

    def get_payload(self, token: str) -> Optional[str]:
        return self._redis.get(f"{_KEY_PREFIX}{token}")

    def is_duplicate(self, token: str) -> bool:
        return self._redis.exists(f"{_KEY_PREFIX}{token}") == 1


idempotency_service = IdempotencyService()
