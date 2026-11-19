from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
UTC = timezone.utc

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.orm.idempotency import IdempotencyKeyORM
from app.models.orm.payment import PaymentTransactionORM

IN_FLIGHT_PAYMENT_STATUSES = {"pending"}

logger = logging.getLogger(__name__)


class IdempotencyMetrics:
    hits: int = 0
    misses: int = 0
    stored: int = 0
    deleted: int = 0
    expired_cleaned: int = 0


metrics = IdempotencyMetrics()


class IdempotencyService:
    def __init__(self, db: Session):
        self.db = db

    def lookup(self, key: str) -> dict | None:
        row = (
            self.db.query(IdempotencyKeyORM)
            .filter(IdempotencyKeyORM.key == key)
            .first()
        )
        if row is None:
            metrics.misses += 1
            return None
        if row.expires_at < datetime.now(UTC):
            metrics.misses += 1
            self.db.delete(row)
            self.db.commit()
            return None
        metrics.hits += 1
        return {
            "response_json": json.loads(row.response_json),
            "status_code": row.status_code,
        }

    def store(
        self, key: str, response_body: dict, status_code: int
    ) -> None:
        ttl_hours = settings.IDEMPOTENCY_KEY_TTL_HOURS
        now = datetime.now(UTC)
        row = IdempotencyKeyORM(
            key=key,
            response_json=json.dumps(response_body),
            status_code=status_code,
            created_at=now,
            expires_at=now + timedelta(hours=ttl_hours),
        )
        self.db.merge(row)
        self.db.commit()
        metrics.stored += 1

    def cleanup_expired(self) -> int:
        now = datetime.now(UTC)
        expired = (
            self.db.query(IdempotencyKeyORM)
            .filter(IdempotencyKeyORM.expires_at < now)
            .all()
        )
        if not expired:
            return 0

        expired_keys = [row.key for row in expired]
        in_flight_keys = {
            key
            for key, in (
                self.db.query(PaymentTransactionORM.idempotency_key)
                .filter(
                    PaymentTransactionORM.idempotency_key.in_(expired_keys),
                    PaymentTransactionORM.status.in_(IN_FLIGHT_PAYMENT_STATUSES),
                )
                .all()
            )
        }

        removed = 0
        for row in expired:
            if row.key in in_flight_keys:
                continue
            self.db.delete(row)
            removed += 1
        self.db.commit()
        metrics.expired_cleaned += removed
        logger.info("Cleaned up %d expired idempotency keys", removed)
        return removed

    def get_metrics(self) -> dict:
        return {
            "hits": metrics.hits,
            "misses": metrics.misses,
            "stored": metrics.stored,
            "expired_cleaned": metrics.expired_cleaned,
        }
