"""Tests for wallet balance caching with freshness guarantees and fallback (#283).

Covers:
- TTLCache.get_with_meta: miss / fresh hit / stale hit without eviction
- BalanceFetchAdapter: cache hit, miss, force-refresh, stale-fallback, FETCH_FAILED
- GET /api/v1/wallets/{address}/balance: freshness metadata, 503 on FETCH_FAILED,
  200 with error fields on stale fallback
"""
from __future__ import annotations

import time
from datetime import datetime, UTC
from unittest.mock import patch

import pytest

from app.main import app
from app.core.security import require_engineer
from app.models.wallet import AssetBalance, WalletLinkRequest
from app.services.contracts.sla_adapter import (
    BalanceFetchAdapter,
    balance_fetch_adapter,
)
from app.services.wallet_registry import WalletRegistry
from app.utils.cache import CacheResult, TTLCache

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Valid Stellar public key format: G + 55 chars from [A-Z2-7]
FAKE_ADDRESS = "G" + "A" * 55


def _register_wallet(
    address: str = FAKE_ADDRESS,
    user_id: str = "test-user-283",
    funded: bool = True,
    trustline: bool = False,
) -> None:
    WalletRegistry.link_wallet(
        WalletLinkRequest(
            user_id=user_id,
            public_key=address,
            funded=funded,
            trustline_ready=trustline,
        )
    )


def _fresh_asset_balances() -> dict:
    return {"XLM": AssetBalance(balance="1.0000000", asset_type="native")}


def _make_adapter(ttl: int = 60) -> BalanceFetchAdapter:
    return BalanceFetchAdapter(cache=TTLCache(ttl_seconds=ttl))


def _force_expire(cache: TTLCache, key: str) -> None:
    """Backdates the expiry timestamp so the entry is immediately stale."""
    with cache._lock:
        val, _ = cache._store[key]
        cache._store[key] = (val, time.monotonic() - 1)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _mock_engineer():
    return None


@pytest.fixture(autouse=True)
def override_auth():
    app.dependency_overrides[require_engineer] = _mock_engineer
    yield
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def clean_state():
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


# ---------------------------------------------------------------------------
# TTLCache.get_with_meta
# ---------------------------------------------------------------------------


class TestGetWithMeta:
    def test_returns_none_for_absent_key(self):
        cache = TTLCache(ttl_seconds=60)
        assert cache.get_with_meta("missing") is None

    def test_returns_result_for_fresh_entry(self):
        cache = TTLCache(ttl_seconds=60)
        cache.set("k", {"v": 1})
        result = cache.get_with_meta("k")
        assert isinstance(result, CacheResult)
        assert result.value == {"v": 1}
        assert not result.is_expired
        assert result.ttl_remaining > 0
        assert result.age_seconds >= 0

    def test_returns_stale_entry_without_evicting(self):
        cache = TTLCache(ttl_seconds=60)
        cache.set("k", {"v": 1})
        _force_expire(cache, "k")
        result = cache.get_with_meta("k")
        assert result is not None
        assert result.is_expired
        assert result.ttl_remaining == 0.0
        # Entry must survive in the store so fallback reads can use it
        assert "k" in cache._store

    def test_regular_get_still_evicts_expired_entries(self):
        cache = TTLCache(ttl_seconds=60)
        cache.set("k", {"v": 1})
        _force_expire(cache, "k")
        assert cache.get("k") is None
        assert "k" not in cache._store

    def test_returns_none_after_explicit_invalidate(self):
        cache = TTLCache(ttl_seconds=60)
        cache.set("k", {"v": 1})
        cache.invalidate("k")
        assert cache.get_with_meta("k") is None


# ---------------------------------------------------------------------------
# BalanceFetchAdapter
# ---------------------------------------------------------------------------


class TestBalanceFetchAdapter:
    def test_cache_miss_calls_live_fetcher(self):
        adapter = _make_adapter()
        calls: list[str] = []

        def live(addr: str) -> dict:
            calls.append(addr)
            return _fresh_asset_balances()

        result = adapter.fetch(FAKE_ADDRESS, live)
        assert result.source == "live"
        assert not result.is_degraded
        assert result.error is None
        assert len(calls) == 1

    def test_cache_hit_skips_live_fetcher(self):
        adapter = _make_adapter()
        calls: list[str] = []

        def live(addr: str) -> dict:
            calls.append(addr)
            return _fresh_asset_balances()

        adapter.fetch(FAKE_ADDRESS, live)       # populates cache
        result = adapter.fetch(FAKE_ADDRESS, live)  # should hit cache
        assert result.source == "cache"
        assert not result.is_degraded
        assert len(calls) == 1                  # live called only once

    def test_force_refresh_bypasses_valid_cache(self):
        adapter = _make_adapter()
        calls: list[str] = []

        def live(addr: str) -> dict:
            calls.append(addr)
            return _fresh_asset_balances()

        adapter.fetch(FAKE_ADDRESS, live)
        result = adapter.fetch(FAKE_ADDRESS, live, force_refresh=True)
        assert result.source == "live"
        assert len(calls) == 2

    def test_stale_fallback_when_live_fails(self):
        adapter = _make_adapter(ttl=60)
        sentinel = datetime.now(UTC)
        adapter._cache.set(
            FAKE_ADDRESS,
            {"balances": _fresh_asset_balances(), "cached_at": sentinel},
        )
        _force_expire(adapter._cache, FAKE_ADDRESS)

        def failing_live(addr: str) -> dict:
            raise ConnectionError("Horizon unavailable")

        result = adapter.fetch(FAKE_ADDRESS, failing_live)
        assert result.source == "stale_fallback"
        assert result.is_degraded
        assert result.error is not None
        assert result.error.code == "STALE_FALLBACK"
        assert result.ttl_remaining == 0
        assert result.cached_at == sentinel

    def test_fetch_failed_when_no_cache_and_live_fails(self):
        adapter = _make_adapter()

        def failing_live(addr: str) -> dict:
            raise RuntimeError("chain unreachable")

        result = adapter.fetch(FAKE_ADDRESS, failing_live)
        assert result.is_degraded
        assert result.error is not None
        assert result.error.code == "FETCH_FAILED"
        assert result.balances == {}
        assert result.cached_at is None

    def test_invalidate_removes_entry(self):
        adapter = _make_adapter()
        adapter._cache.set(FAKE_ADDRESS, {"balances": {}, "cached_at": datetime.now(UTC)})
        adapter.invalidate(FAKE_ADDRESS)
        assert adapter._cache.get_with_meta(FAKE_ADDRESS) is None


# ---------------------------------------------------------------------------
# GET /api/v1/wallets/{address}/balance endpoint
# ---------------------------------------------------------------------------


class TestWalletBalanceEndpoint:
    def test_404_for_unknown_address(self, client):
        resp = client.get(f"/api/v1/wallets/{FAKE_ADDRESS}/balance")
        assert resp.status_code == 404

    def test_freshness_metadata_present_on_live_fetch(self, client):
        _register_wallet()
        resp = client.get(f"/api/v1/wallets/{FAKE_ADDRESS}/balance")
        assert resp.status_code == 200
        data = resp.json()
        assert data["source"] in ("live", "cache")
        assert data["cache_status"] in ("live", "fresh")
        assert data["cached_at"] is not None
        assert data["error_code"] is None
        assert data["error_detail"] is None

    def test_force_refresh_returns_live_source(self, client):
        _register_wallet()
        resp = client.get(f"/api/v1/wallets/{FAKE_ADDRESS}/balance?refresh=true")
        assert resp.status_code == 200
        assert resp.json()["source"] == "live"

    def test_stale_fallback_returns_200_with_error_fields(self, client):
        _register_wallet()
        # Inject stale data directly into the shared adapter cache
        sentinel = datetime.now(UTC)
        balance_fetch_adapter._cache.set(
            FAKE_ADDRESS,
            {"balances": _fresh_asset_balances(), "cached_at": sentinel},
        )
        _force_expire(balance_fetch_adapter._cache, FAKE_ADDRESS)

        with patch.object(
            WalletRegistry,
            "_build_live_balances",
            side_effect=ConnectionError("Horizon down"),
        ):
            resp = client.get(f"/api/v1/wallets/{FAKE_ADDRESS}/balance")

        assert resp.status_code == 200
        data = resp.json()
        assert data["source"] == "stale_fallback"
        assert data["error_code"] == "STALE_FALLBACK"
        assert data["error_detail"] is not None
        assert data["cache_status"] == "stale"

    def test_503_on_fetch_failed_with_no_cache(self, client):
        _register_wallet()
        # Ensure adapter cache is empty for this address
        balance_fetch_adapter._cache.invalidate(FAKE_ADDRESS)

        with patch.object(
            WalletRegistry,
            "_build_live_balances",
            side_effect=RuntimeError("chain down"),
        ):
            resp = client.get(f"/api/v1/wallets/{FAKE_ADDRESS}/balance")

        assert resp.status_code == 503
        detail = resp.json()["detail"]
        assert detail["code"] == "FETCH_FAILED"
        assert detail["address"] == FAKE_ADDRESS
        assert detail["detail"] is not None
