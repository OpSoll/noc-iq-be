"""Tests for GitHub issues assigned to Mirabel64.

Covers:
- #993: Error Handling: Add a mapped 409 for conflicting deletes
- #996: Outages: Add tests for timeline pagination
- #998: Payments: Add tests for accounting report export
- #999: Wallets: Add tests for balance cache TTL
"""
from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.core.security import require_engineer, require_admin
from app.models.enums import OutageStatus, Severity
from app.models.outage_dto import OutageCreate
from app.models.payment import PaymentTransaction
from app.repositories.outage_event_repository import OutageEventRepository
from app.repositories.outage_repository import OutageRepository
from app.services.analytics_exporter import AnalyticsExporter, generate_accounting_report_csv
from app.services.contracts.sla_adapter import BalanceFetchAdapter, balance_fetch_adapter
from app.services.wallet_registry import WalletRegistry
from app.models.wallet import AssetBalance, WalletLinkRequest
from app.utils.cache import CacheResult, TTLCache

# ---------------------------------------------------------------------------
# Auth overrides
# ---------------------------------------------------------------------------

ENGINEER_AUTH = {"Authorization": "Bearer test-engineer-token"}
ADMIN_AUTH = {"Authorization": "Bearer test-admin-token"}


def _mock_engineer():
    return None


def _mock_admin():
    return None


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

FAKE_ADDRESS = "G" + "A" * 55


def _make_outage_create(prefix: str = "", idx: int = 0) -> OutageCreate:
    uid = f"{prefix}-{idx:03d}" if prefix else str(uuid.uuid4())[:12]
    return OutageCreate(
        id=f"out-{uid}",
        site_name=f"Site {uid}",
        site_id=f"site-{uid}",
        severity=Severity.high,
        status=OutageStatus.open,
        detected_at=datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc).replace(minute=idx % 60),
        description=f"Test outage {uid}",
        affected_services=["svc1"],
    )


def _force_expire_cache(cache: TTLCache, key: str) -> None:
    """Backdates a cache entry so it appears stale."""
    import time
    with cache._lock:
        val, _ = cache._store[key]
        cache._store[key] = (val, time.monotonic() - 1)


# ===========================================================================
# Issue #993 — 409 on conflicting deletes
# ===========================================================================


class TestConflictingDeleteReturns409:
    """Issue #993: DELETE /api/v1/outages/{id} must return 409 when the
    outage exists but has already been soft-deleted, not 404."""

    @pytest.fixture(autouse=True)
    def _override_auth(self):
        app.dependency_overrides[require_engineer] = _mock_engineer
        app.dependency_overrides[require_admin] = _mock_admin
        yield
        app.dependency_overrides.clear()

    def test_delete_nonexistent_outage_returns_404(self, client: TestClient, db: Session):
        """A DELETE for an ID that was never created must return 404."""
        resp = client.delete("/api/v1/outages/does-not-exist-ever", headers=ADMIN_AUTH)
        assert resp.status_code == 404

    def test_first_delete_of_existing_outage_returns_200(self, client: TestClient, db: Session):
        """A first DELETE on a live outage must succeed with 200."""
        repo = OutageRepository(db)
        payload = _make_outage_create(f"del993-{uuid.uuid4().hex[:6]}")
        repo.create(payload)

        resp = client.delete(f"/api/v1/outages/{payload.id}", headers=ADMIN_AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert data["message"] == "Outage deleted successfully"
        assert data["deleted_at"] is not None

    def test_second_delete_of_soft_deleted_outage_returns_409(self, client: TestClient, db: Session):
        """A second DELETE on an already-soft-deleted outage must return 409
        with a structured payload (Issue #993)."""
        repo = OutageRepository(db)
        payload = _make_outage_create(f"del993b-{uuid.uuid4().hex[:6]}")
        repo.create(payload)

        # First delete — should succeed
        first = client.delete(f"/api/v1/outages/{payload.id}", headers=ADMIN_AUTH)
        assert first.status_code == 200

        # Second delete — must return 409, not 404
        second = client.delete(f"/api/v1/outages/{payload.id}", headers=ADMIN_AUTH)
        assert second.status_code == 409

    def test_409_response_has_structured_payload(self, client: TestClient, db: Session):
        """409 response must include code, message, and outage_id fields."""
        repo = OutageRepository(db)
        payload = _make_outage_create(f"del993c-{uuid.uuid4().hex[:6]}")
        repo.create(payload)

        client.delete(f"/api/v1/outages/{payload.id}", headers=ADMIN_AUTH)
        resp = client.delete(f"/api/v1/outages/{payload.id}", headers=ADMIN_AUTH)

        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["code"] == "ALREADY_DELETED"
        assert "message" in detail
        assert detail["outage_id"] == payload.id
        assert "deleted_at" in detail

    def test_409_payload_includes_deleted_at_timestamp(self, client: TestClient, db: Session):
        """409 payload must include the deleted_at timestamp of the first delete."""
        repo = OutageRepository(db)
        payload = _make_outage_create(f"del993d-{uuid.uuid4().hex[:6]}")
        repo.create(payload)

        client.delete(f"/api/v1/outages/{payload.id}", headers=ADMIN_AUTH)
        resp = client.delete(f"/api/v1/outages/{payload.id}", headers=ADMIN_AUTH)

        assert resp.status_code == 409
        deleted_at = resp.json()["detail"]["deleted_at"]
        assert deleted_at is not None
        # Must be parseable as ISO 8601
        datetime.fromisoformat(deleted_at)

    def test_409_not_raised_for_404_case(self, client: TestClient, db: Session):
        """Must not return 409 when the outage ID was never created — 404 expected."""
        resp = client.delete("/api/v1/outages/phantom-id-xyz", headers=ADMIN_AUTH)
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Outage not found"

    def test_repository_get_including_deleted_finds_soft_deleted(self, db: Session):
        """OutageRepository.get_including_deleted must return soft-deleted records."""
        repo = OutageRepository(db)
        payload = _make_outage_create(f"repotest993-{uuid.uuid4().hex[:6]}")
        repo.create(payload)
        repo.soft_delete(payload.id)

        # Standard get() must return None for deleted records
        assert repo.get(payload.id) is None

        # get_including_deleted() must return the record
        found = repo.get_including_deleted(payload.id)
        assert found is not None
        assert found.id == payload.id
        assert found.deleted_at is not None

    def test_repository_get_including_deleted_returns_none_for_missing(self, db: Session):
        """get_including_deleted must return None when the ID never existed."""
        repo = OutageRepository(db)
        result = repo.get_including_deleted("never-existed-id")
        assert result is None


# ===========================================================================
# Issue #996 — Timeline pagination tests
# ===========================================================================


class TestTimelinePagination:
    """Issue #996: Tests for page boundaries and ordering of timeline events."""

    @pytest.fixture(autouse=True)
    def _override_auth(self):
        app.dependency_overrides[require_engineer] = _mock_engineer
        app.dependency_overrides[require_admin] = _mock_admin
        yield
        app.dependency_overrides.clear()

    def _seed_outage_with_events(self, db: Session, count: int = 5) -> tuple[str, list[str]]:
        """Create an outage and seed it with `count` timeline events."""
        repo = OutageRepository(db)
        event_repo = OutageEventRepository(db)
        # Use a unique prefix per call to avoid ID collisions across tests
        prefix = uuid.uuid4().hex[:8]
        payload = _make_outage_create(f"tl996-{prefix}")
        repo.create(payload)

        for i in range(count):
            event_repo.record(payload.id, "updated", {"step": i})

        return payload.id, [f"step_{i}" for i in range(count)]

    def test_timeline_returns_total_count(self, client: TestClient, db: Session):
        """Total field must reflect all events regardless of page size."""
        outage_id, _ = self._seed_outage_with_events(db, count=7)

        resp = client.get(
            f"/api/v1/outages/{outage_id}/timeline?page=1&page_size=3",
            headers=ENGINEER_AUTH,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 7

    def test_timeline_page_size_limits_items_returned(self, client: TestClient, db: Session):
        """Items in response must not exceed the requested page_size."""
        outage_id, _ = self._seed_outage_with_events(db, count=8)

        resp = client.get(
            f"/api/v1/outages/{outage_id}/timeline?page=1&page_size=3",
            headers=ENGINEER_AUTH,
        )
        assert resp.status_code == 200
        assert len(resp.json()["items"]) == 3

    def test_timeline_page_two_returns_correct_slice(self, client: TestClient, db: Session):
        """Page 2 must return different items from page 1 (no overlap)."""
        outage_id, _ = self._seed_outage_with_events(db, count=10)

        page1 = client.get(
            f"/api/v1/outages/{outage_id}/timeline?page=1&page_size=4",
            headers=ENGINEER_AUTH,
        ).json()
        page2 = client.get(
            f"/api/v1/outages/{outage_id}/timeline?page=2&page_size=4",
            headers=ENGINEER_AUTH,
        ).json()

        ids1 = {e["id"] for e in page1["items"]}
        ids2 = {e["id"] for e in page2["items"]}
        assert len(ids1) == 4
        assert len(ids2) == 4
        assert ids1.isdisjoint(ids2), "Page 1 and page 2 must not share event IDs"

    def test_timeline_last_page_returns_remainder(self, client: TestClient, db: Session):
        """The final page returns only the remaining events (< page_size)."""
        outage_id, _ = self._seed_outage_with_events(db, count=7)

        resp = client.get(
            f"/api/v1/outages/{outage_id}/timeline?page=2&page_size=5",
            headers=ENGINEER_AUTH,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 2  # 7 events, page 2 of 5 → 2 remaining

    def test_timeline_page_beyond_end_returns_empty(self, client: TestClient, db: Session):
        """Requesting a page past the last page must return an empty items list."""
        outage_id, _ = self._seed_outage_with_events(db, count=3)

        resp = client.get(
            f"/api/v1/outages/{outage_id}/timeline?page=99&page_size=10",
            headers=ENGINEER_AUTH,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["total"] == 3

    def test_timeline_ordering_is_ascending_by_occurred_at(self, client: TestClient, db: Session):
        """Events must be returned oldest-first (ASC by occurred_at)."""
        outage_id, _ = self._seed_outage_with_events(db, count=5)

        resp = client.get(
            f"/api/v1/outages/{outage_id}/timeline?page=1&page_size=10",
            headers=ENGINEER_AUTH,
        )
        assert resp.status_code == 200
        items = resp.json()["items"]
        occurred_ats = [item["occurred_at"] for item in items]
        assert occurred_ats == sorted(occurred_ats), "Events must be in ascending chronological order"

    def test_timeline_page_and_page_size_reflected_in_response(self, client: TestClient, db: Session):
        """Response must echo back the requested page and page_size values."""
        outage_id, _ = self._seed_outage_with_events(db, count=6)

        resp = client.get(
            f"/api/v1/outages/{outage_id}/timeline?page=2&page_size=3",
            headers=ENGINEER_AUTH,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["page"] == 2
        assert data["page_size"] == 3

    def test_timeline_for_unknown_outage_returns_404(self, client: TestClient, db: Session):
        """Timeline of a non-existent outage must return 404."""
        resp = client.get(
            "/api/v1/outages/does-not-exist/timeline",
            headers=ENGINEER_AUTH,
        )
        assert resp.status_code == 404

    def test_timeline_empty_outage_returns_zero_total(self, client: TestClient, db: Session):
        """An outage with no events must return total=0 and items=[]."""
        repo = OutageRepository(db)
        payload = _make_outage_create("tl996empty")
        # Record a creation event; we then test with just that outage
        outage_id = payload.id
        repo.create(payload)

        # Directly query the event repo with a type filter that matches nothing
        resp = client.get(
            f"/api/v1/outages/{outage_id}/timeline?event_type=nonexistent_type",
            headers=ENGINEER_AUTH,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["total"] == 0


# ===========================================================================
# Issue #998 — Accounting report export tests
# ===========================================================================


def _make_payment(
    idx: int = 1,
    amount: float = 100.0,
    status: str = "confirmed",
    asset_code: str = "USDC",
) -> PaymentTransaction:
    return PaymentTransaction(
        id=f"pay-998-{idx:04d}",
        transaction_hash=f"tx-998-{idx:04d}",
        type="reward",
        amount=amount,
        asset_code=asset_code,
        from_address="SYSTEM_POOL",
        to_address="OUTAGE_SETTLEMENT",
        status=status,
        outage_id=f"outage-998-{idx:04d}",
        created_at=datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc),
        confirmed_at=datetime(2026, 3, 1, 12, 5, 0, tzinfo=timezone.utc),
    )


class TestAccountingReportExport:
    """Issue #998: Tests for totals, row counts, and empty-period handling
    in the accounting report export."""

    # ---- generate_accounting_report_csv unit tests ----

    def test_csv_header_row_is_present(self):
        """CSV output must include a header row with expected column names."""
        payments = [_make_payment(1)]
        output = generate_accounting_report_csv(payments)
        reader = csv.reader(io.StringIO(output))
        header = next(reader)
        assert "Transaction ID" in header
        assert "Amount" in header
        assert "Status" in header
        assert "Asset" in header

    def test_csv_row_count_matches_payment_count(self):
        """Number of data rows must equal the number of payments provided."""
        payments = [_make_payment(i) for i in range(1, 6)]
        output = generate_accounting_report_csv(payments)
        reader = csv.reader(io.StringIO(output))
        rows = list(reader)
        # rows[0] is the header; rows[1:] are data rows
        assert len(rows) - 1 == 5

    def test_csv_amounts_are_correct(self):
        """Amount column values must match the input payment amounts."""
        payments = [
            _make_payment(1, amount=250.0),
            _make_payment(2, amount=75.5),
        ]
        output = generate_accounting_report_csv(payments)
        reader = csv.reader(io.StringIO(output))
        header = next(reader)
        amount_idx = header.index("Amount")
        amounts = [float(row[amount_idx]) for row in reader]
        assert amounts == [250.0, 75.5]

    def test_csv_aggregate_total_is_sum_of_amounts(self):
        """Sum of Amount column values must equal the total of all payment amounts."""
        payments = [_make_payment(i, amount=float(i * 10)) for i in range(1, 6)]
        expected_total = sum(i * 10 for i in range(1, 6))  # 10+20+30+40+50 = 150
        output = generate_accounting_report_csv(payments)
        reader = csv.reader(io.StringIO(output))
        header = next(reader)
        amount_idx = header.index("Amount")
        total = sum(float(row[amount_idx]) for row in reader)
        assert total == expected_total

    def test_csv_empty_payment_list_returns_header_only(self):
        """An empty payment list must produce a CSV with only the header row."""
        output = generate_accounting_report_csv([])
        reader = csv.reader(io.StringIO(output))
        rows = list(reader)
        assert len(rows) == 1  # header only
        assert "Transaction ID" in rows[0]

    def test_csv_outage_id_matches_payment(self):
        """Each row's Outage ID column must match the corresponding payment."""
        payments = [_make_payment(1)]
        output = generate_accounting_report_csv(payments)
        reader = csv.reader(io.StringIO(output))
        header = next(reader)
        outage_idx = header.index("Outage ID")
        row = next(reader)
        assert row[outage_idx] == payments[0].outage_id

    def test_csv_transaction_id_matches_payment(self):
        """Each row's Transaction ID column must match the payment ID."""
        payments = [_make_payment(42)]
        output = generate_accounting_report_csv(payments)
        reader = csv.reader(io.StringIO(output))
        header = next(reader)
        id_idx = header.index("Transaction ID")
        row = next(reader)
        assert row[id_idx] == payments[0].id

    def test_csv_status_column_reflects_payment_status(self):
        """Status column must match the payment's status field."""
        payments = [_make_payment(1, status="pending")]
        output = generate_accounting_report_csv(payments)
        reader = csv.reader(io.StringIO(output))
        header = next(reader)
        status_idx = header.index("Status")
        row = next(reader)
        assert row[status_idx] == "pending"

    # ---- AnalyticsExporter.export() tests ----

    def test_analytics_exporter_returns_csv_string(self):
        """AnalyticsExporter.export('csv', ...) must return a non-empty string."""
        payments = [_make_payment(1, amount=100.0)]
        exporter = AnalyticsExporter()
        result = exporter.export(
            "csv", payments,
            datetime(2026, 3, 1, tzinfo=timezone.utc),
            datetime(2026, 3, 31, tzinfo=timezone.utc),
        )
        assert isinstance(result, str)
        assert len(result) > 0

    def test_analytics_exporter_empty_period_returns_header_only(self):
        """An empty payment list must produce header-only CSV, not raise."""
        exporter = AnalyticsExporter()
        result = exporter.export(
            "csv", [],
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 1, 31, tzinfo=timezone.utc),
        )
        rows = list(csv.reader(io.StringIO(result)))
        assert len(rows) == 1  # header only
        assert rows[0][0] == "Transaction ID"

    def test_analytics_exporter_pdf_raises_not_implemented(self):
        """PDF export must raise NotImplementedError (not yet implemented)."""
        exporter = AnalyticsExporter()
        with pytest.raises(NotImplementedError):
            exporter.export(
                "pdf", [],
                datetime(2026, 1, 1, tzinfo=timezone.utc),
                datetime(2026, 1, 31, tzinfo=timezone.utc),
            )

    def test_analytics_exporter_unsupported_format_raises(self):
        """An unsupported format must raise ValueError."""
        exporter = AnalyticsExporter()
        with pytest.raises(ValueError):
            exporter.export(
                "xlsx", [],
                datetime(2026, 1, 1, tzinfo=timezone.utc),
                datetime(2026, 1, 31, tzinfo=timezone.utc),
            )

    def test_csv_confirmed_at_is_included_when_present(self):
        """confirmed_at must appear in the CSV row when set on the payment."""
        payments = [_make_payment(1)]
        output = generate_accounting_report_csv(payments)
        reader = csv.reader(io.StringIO(output))
        header = next(reader)
        confirmed_idx = header.index("Confirmed At")
        row = next(reader)
        assert row[confirmed_idx] != "N/A"

    def test_csv_confirmed_at_is_na_when_missing(self):
        """confirmed_at must appear as 'N/A' when the field is None."""
        p = _make_payment(1)
        p.confirmed_at = None
        output = generate_accounting_report_csv([p])
        reader = csv.reader(io.StringIO(output))
        header = next(reader)
        confirmed_idx = header.index("Confirmed At")
        row = next(reader)
        assert row[confirmed_idx] == "N/A"


# ===========================================================================
# Issue #999 — Wallet balance cache TTL tests
# ===========================================================================


class TestWalletBalanceCacheTTL:
    """Issue #999: Tests for cache hits, TTL expiry, and forced refresh
    in the wallet balance caching layer."""

    @pytest.fixture(autouse=True)
    def _clean_state(self):
        """Reset registry and shared adapter cache before and after every test."""
        WalletRegistry._wallets_by_user.clear()
        WalletRegistry._wallets_by_address.clear()
        WalletRegistry._link_locks.clear()
        balance_fetch_adapter._cache._store.clear()
        yield
        WalletRegistry._wallets_by_user.clear()
        WalletRegistry._wallets_by_address.clear()
        WalletRegistry._link_locks.clear()
        balance_fetch_adapter._cache._store.clear()

    def _register_wallet(self) -> None:
        WalletRegistry.link_wallet(
            WalletLinkRequest(
                user_id="ttl-test-user",
                public_key=FAKE_ADDRESS,
                funded=True,
                trustline_ready=False,
            )
        )

    def _make_adapter(self, ttl: int = 60) -> BalanceFetchAdapter:
        return BalanceFetchAdapter(cache=TTLCache(ttl_seconds=ttl))

    def _fresh_balances(self) -> dict:
        return {"XLM": AssetBalance(balance="5.0000000", asset_type="native")}

    def _force_expire(self, cache: TTLCache, key: str) -> None:
        import time
        with cache._lock:
            val, _ = cache._store[key]
            cache._store[key] = (val, time.monotonic() - 1)

    # ---- Cache hit ----

    def test_cache_hit_returns_source_cache(self):
        """Second fetch for the same address must be served from cache."""
        adapter = self._make_adapter(ttl=60)
        call_count = 0

        def live(addr: str) -> dict:
            nonlocal call_count
            call_count += 1
            return self._fresh_balances()

        adapter.fetch(FAKE_ADDRESS, live)  # populates cache
        result = adapter.fetch(FAKE_ADDRESS, live)  # should hit cache

        assert result.source == "cache"
        assert not result.is_degraded
        assert call_count == 1  # live fetcher called only once

    def test_cache_hit_does_not_increment_live_calls(self):
        """After a cache is populated, repeated calls must not re-invoke the live fetcher."""
        adapter = self._make_adapter(ttl=60)
        calls: list[str] = []

        def live(addr: str) -> dict:
            calls.append(addr)
            return self._fresh_balances()

        for _ in range(5):
            adapter.fetch(FAKE_ADDRESS, live)

        assert len(calls) == 1, "Live fetcher must only be called once regardless of repeat fetches"

    def test_cache_hit_result_is_not_expired(self):
        """A freshly cached entry must not be flagged as expired."""
        adapter = self._make_adapter(ttl=60)

        def live(addr: str) -> dict:
            return self._fresh_balances()

        adapter.fetch(FAKE_ADDRESS, live)  # populate
        result = adapter.fetch(FAKE_ADDRESS, live)  # hit

        meta = adapter._cache.get_with_meta(FAKE_ADDRESS)
        assert meta is not None
        assert not meta.is_expired
        assert meta.ttl_remaining > 0

    # ---- TTL expiry ----

    def test_ttl_expiry_marks_entry_as_stale(self):
        """After backdating the expiry, get_with_meta must flag the entry as expired."""
        adapter = self._make_adapter(ttl=60)

        def live(addr: str) -> dict:
            return self._fresh_balances()

        adapter.fetch(FAKE_ADDRESS, live)
        self._force_expire(adapter._cache, FAKE_ADDRESS)

        meta = adapter._cache.get_with_meta(FAKE_ADDRESS)
        assert meta is not None
        assert meta.is_expired
        assert meta.ttl_remaining == 0.0

    def test_regular_get_evicts_expired_entry(self):
        """After TTL expiry, the regular .get() method must evict and return None."""
        adapter = self._make_adapter(ttl=60)

        def live(addr: str) -> dict:
            return self._fresh_balances()

        adapter.fetch(FAKE_ADDRESS, live)
        self._force_expire(adapter._cache, FAKE_ADDRESS)

        assert adapter._cache.get(FAKE_ADDRESS) is None
        assert FAKE_ADDRESS not in adapter._cache._store

    def test_expired_entry_triggers_new_live_fetch(self):
        """After TTL expiry, the next fetch must call the live fetcher again."""
        adapter = self._make_adapter(ttl=60)
        calls: list[str] = []

        def live(addr: str) -> dict:
            calls.append(addr)
            return self._fresh_balances()

        adapter.fetch(FAKE_ADDRESS, live)  # first fetch → live
        self._force_expire(adapter._cache, FAKE_ADDRESS)
        # regular .get() evicts the stale entry
        adapter._cache.get(FAKE_ADDRESS)
        adapter.fetch(FAKE_ADDRESS, live)  # second fetch → live again

        assert len(calls) == 2

    def test_stale_entry_survives_get_with_meta(self):
        """get_with_meta must NOT evict the stale entry — fallback reads need it."""
        adapter = self._make_adapter(ttl=60)

        def live(addr: str) -> dict:
            return self._fresh_balances()

        adapter.fetch(FAKE_ADDRESS, live)
        self._force_expire(adapter._cache, FAKE_ADDRESS)

        # get_with_meta should return the stale entry without evicting it
        meta = adapter._cache.get_with_meta(FAKE_ADDRESS)
        assert meta is not None
        assert meta.is_expired
        assert FAKE_ADDRESS in adapter._cache._store  # still present

    # ---- Forced refresh ----

    def test_force_refresh_bypasses_valid_cache(self):
        """force_refresh=True must bypass a non-expired cache entry."""
        adapter = self._make_adapter(ttl=60)
        calls: list[str] = []

        def live(addr: str) -> dict:
            calls.append(addr)
            return self._fresh_balances()

        adapter.fetch(FAKE_ADDRESS, live)  # populate cache
        result = adapter.fetch(FAKE_ADDRESS, live, force_refresh=True)

        assert result.source == "live"
        assert len(calls) == 2  # called twice: once to populate, once for forced refresh

    def test_force_refresh_updates_cache_with_fresh_data(self):
        """After a forced refresh, the cache must contain the updated data."""
        adapter = self._make_adapter(ttl=60)
        fetch_count = 0

        def live(addr: str) -> dict:
            nonlocal fetch_count
            fetch_count += 1
            return {"XLM": AssetBalance(balance=f"{fetch_count}.0000000", asset_type="native")}

        adapter.fetch(FAKE_ADDRESS, live)  # fetch_count = 1
        adapter.fetch(FAKE_ADDRESS, live, force_refresh=True)  # fetch_count = 2

        # Cache must now contain the data from the second fetch
        next_result = adapter.fetch(FAKE_ADDRESS, live)  # should hit cache
        assert next_result.source == "cache"
        assert fetch_count == 2  # no additional live fetch

    def test_force_refresh_on_expired_entry_refreshes(self):
        """force_refresh=True must also work after TTL expiry."""
        adapter = self._make_adapter(ttl=60)
        calls: list[str] = []

        def live(addr: str) -> dict:
            calls.append(addr)
            return self._fresh_balances()

        adapter.fetch(FAKE_ADDRESS, live)  # populate
        self._force_expire(adapter._cache, FAKE_ADDRESS)

        result = adapter.fetch(FAKE_ADDRESS, live, force_refresh=True)
        assert result.source == "live"

    # ---- TTLCache.get_with_meta behaviour ----

    def test_ttl_cache_miss_returns_none(self):
        """Cache must return None for a key that was never inserted."""
        cache = TTLCache(ttl_seconds=60)
        assert cache.get_with_meta("absent") is None

    def test_ttl_cache_fresh_entry_has_positive_ttl_remaining(self):
        """A freshly inserted entry must have a positive ttl_remaining."""
        cache = TTLCache(ttl_seconds=60)
        cache.set("k", {"v": 1})
        result = cache.get_with_meta("k")
        assert isinstance(result, CacheResult)
        assert result.ttl_remaining > 0
        assert not result.is_expired

    def test_ttl_cache_invalidate_removes_entry(self):
        """After invalidation, get_with_meta must return None."""
        cache = TTLCache(ttl_seconds=60)
        cache.set("k", {"v": 1})
        cache.invalidate("k")
        assert cache.get_with_meta("k") is None
