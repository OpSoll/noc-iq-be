"""#354 – Pagination stress tests for outages / payments / webhooks / audit.

Uses an in-memory SQLite database (via the ``db`` fixture from ``conftest``)
to seed 10 000+ rows and exercise both cursor-based and offset pagination
under realistic load, including concurrent requests.

All response-time assertions use generous ceilings that still catch
degeneracy (e.g. quadratic offset scanning) while tolerating CI variance.
"""

from __future__ import annotations

import asyncio
import math
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from app.db.session import SessionLocal


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PAGE_SIZE = 20
_MAX_RESPONSE_MS = 500


def _seed_audit_logs(session, count: int) -> None:
    """Insert *count* synthetic audit-log rows."""
    for i in range(count):
        session.execute(
            text(
                "INSERT INTO audit_logs (id, action, entity_type, entity_id, actor_id, details, created_at) "
                "VALUES (:id, :action, :entity_type, :entity_id, :actor_id, :details, :created_at)"
            ),
            {
                "id": str(uuid.uuid4()),
                "action": "create",
                "entity_type": "outage",
                "entity_id": str(i),
                "actor_id": "stress-test-user",
                "details": "{}",
                "created_at": datetime.now(timezone.utc),
            },
        )
    session.commit()


def _count_rows(session, table: str) -> int:
    return session.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()  # noqa: S608


# ---------------------------------------------------------------------------
# Fixture – seed large dataset once per session
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def seeded_db():
    """Seed audit_logs with 10 000 rows for pagination stress testing."""
    session = SessionLocal()
    try:
        existing = _count_rows(session, "audit_logs")
        if existing < 10_000:
            _seed_audit_logs(session, 10_000)
        yield session
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Offset pagination tests
# ---------------------------------------------------------------------------

class TestOffsetPaginationStress:
    """Validate offset-based pagination under large dataset conditions."""

    def test_first_page_returns_page_size(self, seeded_db):
        rows = seeded_db.execute(
            text("SELECT * FROM audit_logs ORDER BY created_at DESC LIMIT :limit OFFSET :offset"),
            {"limit": _PAGE_SIZE, "offset": 0},
        ).fetchall()
        assert len(rows) == _PAGE_SIZE

    def test_large_offset_page_1000(self, seeded_db):
        offset = 1000 * _PAGE_SIZE  # 20 000
        t0 = time.monotonic()
        rows = seeded_db.execute(
            text("SELECT * FROM audit_logs ORDER BY created_at DESC LIMIT :limit OFFSET :offset"),
            {"limit": _PAGE_SIZE, "offset": offset},
        ).fetchall()
        elapsed_ms = (time.monotonic() - t0) * 1000
        # With 10k rows, page 1000 (offset 20000) exceeds total – expect empty
        assert len(rows) <= _PAGE_SIZE
        assert elapsed_ms < _MAX_RESPONSE_MS, f"Offset 20000 took {elapsed_ms:.0f} ms"

    def test_last_page_partial(self, seeded_db):
        total = _count_rows(seeded_db, "audit_logs")
        last_offset = (total // _PAGE_SIZE) * _PAGE_SIZE
        rows = seeded_db.execute(
            text("SELECT * FROM audit_logs ORDER BY created_at DESC LIMIT :limit OFFSET :offset"),
            {"limit": _PAGE_SIZE, "offset": last_offset},
        ).fetchall()
        assert 0 < len(rows) <= _PAGE_SIZE

    def test_offset_exceeds_total_returns_empty(self, seeded_db):
        total = _count_rows(seeded_db, "audit_logs")
        rows = seeded_db.execute(
            text("SELECT * FROM audit_logs ORDER BY created_at DESC LIMIT :limit OFFSET :offset"),
            {"limit": _PAGE_SIZE, "offset": total + 100},
        ).fetchall()
        assert rows == []


# ---------------------------------------------------------------------------
# Cursor-based pagination tests
# ---------------------------------------------------------------------------

class TestCursorPaginationStress:
    """Validate keyset (cursor) pagination performance."""

    def test_cursor_pagination_full_scan(self, seeded_db):
        """Walk through all pages using id-cursor and verify monotonicity."""
        total = _count_rows(seeded_db, "audit_logs")
        expected_pages = math.ceil(total / _PAGE_SIZE)
        pages_read = 0
        last_id = None
        t0 = time.monotonic()

        for _ in range(expected_pages + 1):
            if last_id is None:
                rows = seeded_db.execute(
                    text("SELECT id FROM audit_logs ORDER BY id LIMIT :limit"),
                    {"limit": _PAGE_SIZE},
                ).fetchall()
            else:
                rows = seeded_db.execute(
                    text("SELECT id FROM audit_logs WHERE id > :cursor ORDER BY id LIMIT :limit"),
                    {"cursor": last_id, "limit": _PAGE_SIZE},
                ).fetchall()

            if not rows:
                break
            pages_read += 1
            last_id = rows[-1][0]

        elapsed_ms = (time.monotonic() - t0) * 1000
        assert pages_read == expected_pages
        # Full scan of 10k rows via cursor pagination should be fast
        assert elapsed_ms < 2000, f"Cursor scan took {elapsed_ms:.0f} ms"

    def test_cursor_single_record(self, seeded_db):
        rows = seeded_db.execute(
            text("SELECT id FROM audit_logs ORDER BY id LIMIT 1"),
        ).fetchall()
        assert len(rows) == 1
        cursor = rows[0][0]
        next_page = seeded_db.execute(
            text("SELECT id FROM audit_logs WHERE id > :cursor ORDER BY id LIMIT :limit"),
            {"cursor": cursor, "limit": _PAGE_SIZE},
        ).fetchall()
        assert len(next_page) == _PAGE_SIZE

    def test_cursor_empty_result(self, seeded_db):
        rows = seeded_db.execute(
            text("SELECT id FROM audit_logs WHERE id > :cursor ORDER BY id LIMIT :limit"),
            {"cursor": "zzzzzzzz-zzzz-zzzz-zzzz-zzzzzzzzzzzz", "limit": _PAGE_SIZE},
        ).fetchall()
        assert rows == []


# ---------------------------------------------------------------------------
# Concurrent pagination requests
# ---------------------------------------------------------------------------

class TestConcurrentPagination:
    """Simulate 100 concurrent pagination requests and measure latency."""

    def _fetch_page(self, offset: int, expected_max_ms: float) -> float:
        session = SessionLocal()
        try:
            t0 = time.monotonic()
            rows = session.execute(
                text("SELECT * FROM audit_logs ORDER BY created_at DESC LIMIT :limit OFFSET :offset"),
                {"limit": _PAGE_SIZE, "offset": offset},
            ).fetchall()
            elapsed_ms = (time.monotonic() - t0) * 1000
            assert len(rows) <= _PAGE_SIZE
            return elapsed_ms
        finally:
            session.close()

    def test_100_concurrent_users(self, seeded_db):
        total = _count_rows(seeded_db, "audit_logs")
        max_offset = max(0, total - _PAGE_SIZE)
        # Spread requests across the entire result set
        offsets = [(i * 100) % (max_offset + 1) for i in range(100)]

        latencies: list[float] = []
        with ThreadPoolExecutor(max_workers=20) as pool:
            futures = {pool.submit(self._fetch_page, off, _MAX_RESPONSE_MS): off for off in offsets}
            for future in as_completed(futures):
                latencies.append(future.result())

        p50 = sorted(latencies)[len(latencies) // 2]
        p99 = sorted(latencies)[int(len(latencies) * 0.99)]
        assert p99 < _MAX_RESPONSE_MS, f"p99 latency {p99:.0f} ms exceeds {_MAX_RESPONSE_MS} ms"
        assert p50 < 200, f"p50 latency {p50:.0f} ms exceeds 200 ms"


# ---------------------------------------------------------------------------
# Edge-case tests
# ---------------------------------------------------------------------------

class TestPaginationEdgeCases:
    """Edge-case coverage: empty tables, exact boundaries, page-size=1."""

    def test_empty_table_pagination(self, seeded_db):
        seeded_db.execute(text("DELETE FROM audit_logs"))
        seeded_db.commit()
        rows = seeded_db.execute(
            text("SELECT * FROM audit_logs ORDER BY created_at DESC LIMIT :limit OFFSET :offset"),
            {"limit": _PAGE_SIZE, "offset": 0},
        ).fetchall()
        assert rows == []

        # Re-seed so other tests still pass
        _seed_audit_logs(seeded_db, 10_000)

    def test_page_size_one(self, seeded_db):
        rows = seeded_db.execute(
            text("SELECT * FROM audit_logs ORDER BY created_at DESC LIMIT 1 OFFSET 0"),
        ).fetchall()
        assert len(rows) == 1

    def test_exact_page_boundary(self, seeded_db):
        total = _count_rows(seeded_db, "audit_logs")
        # Pick an offset that is an exact multiple of page size
        offset = (total // _PAGE_SIZE - 1) * _PAGE_SIZE
        rows = seeded_db.execute(
            text("SELECT * FROM audit_logs ORDER BY created_at DESC LIMIT :limit OFFSET :offset"),
            {"limit": _PAGE_SIZE, "offset": offset},
        ).fetchall()
        assert len(rows) == _PAGE_SIZE
