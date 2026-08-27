from __future__ import annotations

import logging
import time
from typing import Generator, Optional

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

logger = logging.getLogger(__name__)

import os

_DB_URL = os.getenv("DATABASE_URL") or os.getenv("SQLALCHEMY_DATABASE_URI") or ("sqlite:///./test_nociq.db" if os.getenv("TESTING") or os.getenv("PYTEST_CURRENT_TEST") else "sqlite:///./nociq.db")

# check_same_thread and the PRAGMA statements below are SQLite-specific and
# must not be applied to other backends (e.g. PostgreSQL).
_is_sqlite = make_url(_DB_URL).get_backend_name() == "sqlite"
_connect_args = {"check_same_thread": False} if _is_sqlite else {}

# Issue #526: honour the configured transaction isolation level on the
# engine. SQLite only supports SERIALIZABLE, so the option is only passed
# through for real (PostgreSQL) backends.
_engine_isolation_level = (
    None if _is_sqlite else settings.DB_TRANSACTION_ISOLATION_LEVEL
)

engine: Engine = create_engine(
    _DB_URL,
    connect_args=_connect_args,
    pool_pre_ping=True,
    isolation_level=_engine_isolation_level,
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):  # type: ignore[no-untyped-def]
    if not _is_sqlite:
        return
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
