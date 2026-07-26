from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, UTC
from uuid import uuid4

import redis

from app.core.config import settings
from app.models.wallet import (
    AssetBalance,
    Wallet,
    WalletBalanceResponse,
    WalletCreateRequest,
    WalletCreateResponse,
    WalletLinkRequest,
    WalletStatusResponse,
)

logger = logging.getLogger(__name__)


class WalletCacheMetrics:
    cache_hits: int = 0
    cache_misses: int = 0
    lock_acquisitions: int = 0
    lock_timeouts: int = 0


_wallet_cache_metrics = WalletCacheMetrics()

_balance_cache: dict[str, tuple[WalletBalanceResponse, float]] = {}


class WalletRegistry:
    _wallets_by_user: dict[str, Wallet] = {}
    _wallets_by_address: dict[str, Wallet] = {}
    _redis_client: redis.Redis | None = None

    @classmethod
    def _get_redis(cls) -> redis.Redis | None:
        if cls._redis_client is None:
            try:
                cls._redis_client = redis.Redis.from_url(
                    settings.REDIS_URL, decode_responses=True, socket_timeout=2
                )
                cls._redis_client.ping()
            except Exception:
                cls._redis_client = None
        return cls._redis_client

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    @classmethod
    def _build_public_key(cls) -> str:
        return f"G{uuid4().hex.upper()}"

    @classmethod
    def _try_acquire_lock(cls, wallet_key: str) -> bool:
        r = cls._get_redis()
        if r is None:
            return True
        lock_key = f"{settings.WALLET_CACHE_LOCK_PREFIX}{wallet_key}"
        for attempt in range(3):
            acquired = r.set(
                lock_key, "1", nx=True, ex=settings.WALLET_CACHE_LOCK_TIMEOUT
            )
            if acquired:
                _wallet_cache_metrics.lock_acquisitions += 1
                return True
            _wallet_cache_metrics.lock_timeouts += 1
            if attempt < 2:
                time.sleep(0.1)
        return False

    @classmethod
    def _release_lock(cls, wallet_key: str) -> None:
        r = cls._get_redis()
        if r is None:
            return
        lock_key = f"{settings.WALLET_CACHE_LOCK_PREFIX}{wallet_key}"
        try:
            r.delete(lock_key)
        except Exception:
            pass

    @classmethod
    def _get_cached_balance(
        cls, address: str
    ) -> WalletBalanceResponse | None:
        cached = _balance_cache.get(address)
        if cached is None:
            return None
        response, ts = cached
        if time.time() - ts > settings.WALLET_CACHE_TTL:
            return None
        return response

    @classmethod
    def _set_cached_balance(
        cls, address: str, response: WalletBalanceResponse
    ) -> None:
        _balance_cache[address] = (response, time.time())

    @classmethod
    def create_wallet(cls, payload: WalletCreateRequest) -> WalletCreateResponse:
        existing = cls._wallets_by_user.get(payload.user_id)
        if existing:
            return WalletCreateResponse(
                **existing.model_dump(),
                message="Wallet already exists for this user.",
            )

        wallet = Wallet(
            user_id=payload.user_id,
            public_key=cls._build_public_key(),
            created_at=cls._now(),
            last_updated=cls._now(),
            funded=False,
            active=True,
            trustline_ready=False,
        )
        cls._wallets_by_user[payload.user_id] = wallet
        cls._wallets_by_address[wallet.public_key] = wallet
        return WalletCreateResponse(
            **wallet.model_dump(),
            message="Wallet created. Please fund with at least 1 XLM to activate.",
        )

    @classmethod
    def link_wallet(cls, payload: WalletLinkRequest) -> Wallet:
        now = cls._now()
        existing = cls._wallets_by_user.get(payload.user_id)
        created_at = existing.created_at if existing else now

        wallet = Wallet(
            user_id=payload.user_id,
            public_key=payload.public_key,
            created_at=created_at,
            last_updated=now,
            funded=payload.funded,
            active=True,
            trustline_ready=payload.trustline_ready,
        )
        cls._wallets_by_user[payload.user_id] = wallet
        cls._wallets_by_address[payload.public_key] = wallet
        return wallet

    @classmethod
    def get_wallet(cls, user_id: str) -> Wallet | None:
        return cls._wallets_by_user.get(user_id)

    @classmethod
    def get_balance(cls, address: str) -> tuple[WalletBalanceResponse | None, str]:
        wallet = cls._wallets_by_address.get(address)
        if not wallet:
            return None, "miss"

        cached = cls._get_cached_balance(address)
        if cached is not None:
            _wallet_cache_metrics.cache_hits += 1
            return cached, "hit"

        _wallet_cache_metrics.cache_misses += 1
        lock_acquired = cls._try_acquire_lock(address)
        if not lock_acquired:
            cached = cls._get_cached_balance(address)
            if cached is not None:
                return cached, "stale"
            return None, "miss"

        try:
            xlm_balance = "1.0000000" if wallet.funded else "0.0000000"
            balances = {
                "XLM": AssetBalance(balance=xlm_balance, asset_type="native"),
            }
            if wallet.trustline_ready:
                balances["USDC"] = AssetBalance(
                    balance="0.0000000",
                    asset_type="credit_alphanum4",
                    asset_code="USDC",
                    asset_issuer="TEST_ISSUER",
                )
            response = WalletBalanceResponse(
                address=address,
                balances=balances,
                last_updated=cls._now(),
            )
            cls._set_cached_balance(address, response)
            return response, "refreshing"
        finally:
            cls._release_lock(address)

    @classmethod
    def get_cache_metrics(cls) -> dict:
        return {
            "cache_hits": _wallet_cache_metrics.cache_hits,
            "cache_misses": _wallet_cache_metrics.cache_misses,
            "lock_acquisitions": _wallet_cache_metrics.lock_acquisitions,
            "lock_timeouts": _wallet_cache_metrics.lock_timeouts,
        }

    @classmethod
    def get_status(cls, user_id: str) -> WalletStatusResponse | None:
        wallet = cls.get_wallet(user_id)
        if not wallet:
            return None

        return WalletStatusResponse(
            user_id=wallet.user_id,
            public_key=wallet.public_key,
            funded=wallet.funded,
            trustline_ready=wallet.trustline_ready,
            usable=wallet.funded and wallet.trustline_ready and wallet.active,
            active=wallet.active,
            last_updated=wallet.last_updated,
        )
