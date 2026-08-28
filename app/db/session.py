from __future__ import annotations

import logging
import time
from typing import Generator, Optional

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

logger = logging.getLogger(__name__)

# Issue #529: retry DB connection on startup when PostgreSQL is still initializing.
DB_CONNECT_MAX_RETRIES = 10
DB_CONNECT_BACKOFF_SECONDS = 2.0

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

# Issue #520: configure pool size and overflow to prevent connection exhaustion
# under high API concurrency.
_engine_kwargs: dict = dict(
    connect_args=_connect_args,
    pool_pre_ping=True,
    isolation_level=_engine_isolation_level,
)
if not _is_sqlite:
    # SQLite uses StaticPool / NullPool and does not support these options.
    _engine_kwargs["pool_size"] = 20
    _engine_kwargs["max_overflow"] = 10

engine: Engine = create_engine(_DB_URL, **_engine_kwargs)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):  # type: ignore[no-untyped-def]
    if not _is_sqlite:
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


# Issue #520: connection pool lifecycle logging and leak detection.
_CHECKOUT_WARN_THRESHOLD_SECONDS = 5.0


@event.listens_for(engine, "checkout")
def _on_checkout(dbapi_connection, connection_record, connection_proxy):  # type: ignore[no-untyped-def]
    """Record checkout timestamp for leak detection."""
    connection_record.info["checkout_at"] = time.monotonic()
    logger.debug("DB connection checked out (pool id=%s)", id(connection_record))


@event.listens_for(engine, "checkin")
def _on_checkin(dbapi_connection, connection_record):  # type: ignore[no-untyped-def]
    """Log checkin duration and warn when a connection was held too long."""
    checkout_at = connection_record.info.pop("checkout_at", None)
    if checkout_at is None:
        return
    duration = time.monotonic() - checkout_at
    if duration >= _CHECKOUT_WARN_THRESHOLD_SECONDS:
        logger.warning(
            "DB connection held for %.2fs (threshold=%.1fs) — possible connection leak",
            duration,
            _CHECKOUT_WARN_THRESHOLD_SECONDS,
        )
    else:
        logger.debug(
            "DB connection checked in after %.3fs (pool id=%s)",
            duration,
            id(connection_record),
        )


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


def _attempt_db_connection() -> None:
    """Verify the database is reachable with a lightweight probe query."""
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))


def connect_db_with_retry(
    max_retries: int = DB_CONNECT_MAX_RETRIES,
    base_backoff: float = DB_CONNECT_BACKOFF_SECONDS,
) -> None:
    """Retry database connection on startup with exponential backoff.

    Issue #529: tolerates slow PostgreSQL container initialization by retrying
    up to ``max_retries`` times with delays of ``base_backoff * 2**(n-1)`` seconds.
    """
    last_exc: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            logger.info(
                "Database connection attempt %d/%d",
                attempt,
                max_retries,
            )
            _attempt_db_connection()
            logger.info(
                "Database connection established on attempt %d/%d",
                attempt,
                max_retries,
            )
            return
        except Exception as exc:
            last_exc = exc
            if attempt >= max_retries:
                logger.error(
                    "Database connection failed after %d attempts: %s",
                    max_retries,
                    exc,
                )
                raise
            delay = base_backoff * (2 ** (attempt - 1))
            logger.warning(
                "Database connection attempt %d/%d failed: %s. Retrying in %.1fs...",
                attempt,
                max_retries,
                exc,
                delay,
            )
            time.sleep(delay)

    if last_exc is not None:
        raise last_exc


def warmup_db_pool() -> None:
    """Eagerly create a connection, retrying until the database is ready."""
    connect_db_with_retry()
    logger.info("Database pool warmed up successfully")


def shutdown_db_pool() -> None:
    """Dispose of all pooled connections."""
    engine.dispose()
    logger.info("Database pool disposed")
