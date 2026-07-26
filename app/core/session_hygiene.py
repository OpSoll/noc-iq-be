"""SQLAlchemy session leak detection and connection pool hygiene audit (#412)."""

from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from typing import Any, Generator

from fastapi import APIRouter, Depends, Request
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import require_admin
from app.db.session import SessionLocal, engine

logger = logging.getLogger("session_hygiene")

# ---------------------------------------------------------------------------
# Per-request session tracking
# ---------------------------------------------------------------------------

_request_sessions: dict[int, list[int]] = {}  # request_id -> list of session ids
_lock = threading.Lock()

# Global lifetime counters for audit
_sessions_created = 0
_sessions_closed = 0
_sessions_leaked = 0

_last_health_check: dict[str, Any] = {}


def _session_id(session: Session) -> int:
    return id(session)


# ---------------------------------------------------------------------------
# SQLAlchemy event listeners for lifecycle tracking
# ---------------------------------------------------------------------------

@event.listens_for(Session, "after_create")
def _on_session_create(session: Session, transaction: Any) -> None:
    global _sessions_created
    _sessions_created += 1
    session_key = f"req:{getattr(session, '_request_id', 'bg')}"
    logger.debug("Session opened [%s] id=%d", session_key, _session_id(session))


@event.listens_for(Session, "after_close")
def _on_session_close(session: Session) -> None:
    global _sessions_closed, _sessions_leaked
    _sessions_closed += 1
    session_key = f"req:{getattr(session, '_request_id', 'bg')}"
    logger.debug("Session closed [%s] id=%d", session_key, _session_id(session))


# ---------------------------------------------------------------------------
# Tracked get_db generator (drop-in replacement)
# ---------------------------------------------------------------------------

def get_db_tracked(request: Request | None = None) -> Generator[Session, None, None]:
    """Yield a tracked session and ensure it is closed in the *finally* block."""
    db = SessionLocal()
    req_id = id(request) if request else None
    if req_id is not None:
        db._request_id = req_id
        with _lock:
            _request_sessions.setdefault(req_id, []).append(_session_id(db))

    try:
        yield db
    finally:
        db.close()
        # Clean up tracking entry
        if req_id is not None:
            with _lock:
                sessions = _request_sessions.get(req_id, [])
                try:
                    sessions.remove(_session_id(db))
                except ValueError:
                    pass
                if not sessions:
                    _request_sessions.pop(req_id, None)


# ---------------------------------------------------------------------------
# Audit helpers
# ---------------------------------------------------------------------------

def session_audit() -> dict[str, Any]:
    """Return connection pool statistics and leak-detection counters."""
    pool = engine.pool
    checked_out = pool.checkedout()
    overflow = pool.overflow()
    size = pool.size()
    checkedin = pool.checkedin()

    with _lock:
        open_request_sessions = {
            req_id: sess_ids
            for req_id, sess_ids in _request_sessions.items()
            if sess_ids
        }

    stats = {
        "pool": {
            "size": size,
            "checked_out": checked_out,
            "checked_in": checkedin,
            "overflow": overflow,
        },
        "lifetime": {
            "created": _sessions_created,
            "closed": _sessions_closed,
            "potential_leaks": _sessions_created - _sessions_closed,
        },
        "open_by_request": len(open_request_sessions),
        "open_session_ids": [
            sid for sids in open_request_sessions.values() for sid in sids
        ],
    }
    return stats


def health_check_sessions() -> dict[str, Any]:
    """Periodic health check that returns current leak-detection counters.

    Returns a dict with ``status`` ``"ok"`` or ``"warning"`` and a ``detail``.
    """
    stats = session_audit()
    leak_count = stats["lifetime"]["potential_leaks"]
    result: dict[str, Any] = {
        "status": "ok",
        "leak_count": leak_count,
        "pool": stats["pool"],
        "open_requests": stats["open_by_request"],
    }
    if leak_count > 10:
        result["status"] = "warning"
        result["detail"] = (
            f"{leak_count} potential session leaks detected since startup."
        )
        logger.warning(
            "Session hygiene warning: %d potential leaks detected.", leak_count
        )
    return result


# ---------------------------------------------------------------------------
# Debug endpoint router
# ---------------------------------------------------------------------------

debug_router = APIRouter(tags=["debug"])


@debug_router.get("/debug/session-stats")
def session_stats(current_user=Depends(require_admin)) -> dict[str, Any]:
    """Return connection pool metrics (admin only)."""
    return session_audit()
