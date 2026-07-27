import time
import logging
from typing import Optional

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool
from __future__ import annotations

import logging
from typing import Generator

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

logger = logging.getLogger(__name__)

DB_URL = settings.REDIS_URL.replace("redis://", "postgresql://") if "redis://" in settings.REDIS_URL else settings.REDIS_URL
DB_URL = "sqlite:///./nociq.db" if "redis://" in settings.REDIS_URL or "postgresql://" not in settings.REDIS_URL else DB_URL

engine: Engine = create_engine(
    DB_URL,
    poolclass=QueuePool,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_POOL_MAX_OVERFLOW,
engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    pool_timeout=30,
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):  # type: ignore[no-untyped-def]
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class PoolHealthChecker:
    def __init__(self, engine_ref: Engine):
        self._engine = engine_ref
        self._saturated_since: Optional[float] = None

    def get_stats(self) -> dict:
        pool = self._engine.pool
        checked_in = pool.checkedin()
        checked_out = pool.checkedout()
        overflow = pool.overflow()
        total = pool.size()
        return {
            "pool_size": total,
            "active": checked_out,
            "idle": checked_in,
            "overflow": overflow,
            "max_overflow": pool._max_overflow,
            "saturation": round(checked_out / max(total + pool._max_overflow, 1), 4),
        }

    def is_saturated(self) -> bool:
        stats = self.get_stats()
        return stats["saturation"] >= settings.DB_POOL_SATURATION_THRESHOLD

    def wait_for_connection(self, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.is_saturated():
                return True
            time.sleep(0.05)
        return False


pool_health = PoolHealthChecker(engine)


def get_db():
def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def warmup_db_pool() -> None:
    """Eagerly create a connection to fail fast on misconfiguration."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        logger.info("Database pool warmed up successfully")
    except Exception:
        logger.exception("Failed to warm up database pool")


def shutdown_db_pool() -> None:
    """Dispose of all pooled connections."""
    engine.dispose()
    logger.info("Database pool disposed")
