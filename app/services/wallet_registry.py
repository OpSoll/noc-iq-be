from __future__ import annotations

from datetime import datetime, UTC, timedelta
from uuid import uuid4

from app.core.config import settings
from app.services.contracts.sla_adapter import BalanceFetchResult, balance_fetch_adapter
from app.models.wallet import (
    AssetBalance,
    Wallet,
    WalletBalanceResponse,
    WalletCreateRequest,
    WalletCreateResponse,
    WalletFundingStateResponse,
    WalletLinkRequest,
    WalletStatusResponse,
    WalletTrustlineResponse,
)
from app.repositories.wallet_repository import WalletRepository, wallet_orm_to_pydantic
from app.db.session import SessionLocal
from sqlalchemy.exc import IntegrityError


class WalletRegistry:
    _wallets_by_user: dict[str, Wallet] = {}  # In-memory cache for performance
    _wallets_by_address: dict[str, Wallet] = {}  # In-memory cache for performance
    _link_locks: dict[str, bool] = {}  # Simple lock mechanism for link operations

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    @classmethod
    def _is_stale(cls, wallet: Wallet) -> bool:
        """Return True if cached_at is absent or older than WALLET_CACHE_TTL_SECONDS."""
        if wallet.cached_at is None:
            return True
        age = (cls._now() - wallet.cached_at).total_seconds()
        return age > settings.WALLET_CACHE_TTL_SECONDS

    @classmethod
    def _get_cache_ttl_remaining(cls, wallet: Wallet) -> int | None:
        """Return seconds until cache expires, or None if stale/never cached."""
        if wallet.cached_at is None:
            return None
        age = (cls._now() - wallet.cached_at).total_seconds()
        remaining = settings.WALLET_CACHE_TTL_SECONDS - age
        return max(0, int(remaining)) if remaining > 0 else None

    @classmethod
    def _refresh_wallet(cls, wallet: Wallet) -> Wallet:
        """Simulate a live re-fetch and stamp cached_at. In production this would
        call the Stellar Horizon API; here we just update the timestamp."""
        now = cls._now()
        refreshed = wallet.model_copy(update={"cached_at": now, "last_updated": now, "cache_status": "live"})
        cls._wallets_by_user[wallet.user_id] = refreshed
        cls._wallets_by_address[wallet.public_key] = refreshed
        return refreshed

    @classmethod
    def _get_db_session(cls):
        """Get a database session for repository operations."""
        return SessionLocal()

    @classmethod
    def _build_live_balances(cls, address: str) -> dict:
        """Compute live balances for address. Raises ValueError if not in registry.

        In production this would call the Stellar Horizon API and propagate
        any network or chain errors to the caller, triggering stale fallback.
        """
        wallet = cls._wallets_by_address.get(address)
        if not wallet:
            # Try to fetch from database if not in cache
            with cls._get_db_session() as db:
                repo = WalletRepository(db)
                wallet_orm = repo.get_by_public_key(address)
                if not wallet_orm:
                    raise ValueError(f"Address {address} not found in registry")
                wallet = wallet_orm_to_pydantic(wallet_orm)
                cls._wallets_by_user[wallet.user_id] = wallet
                cls._wallets_by_address[wallet.public_key] = wallet
        wallet = cls._refresh_wallet(wallet)
        xlm_balance = "1.0000000" if wallet.funded else "0.0000000"
        balances: dict = {"XLM": AssetBalance(balance=xlm_balance, asset_type="native")}
        if wallet.trustline_ready:
            balances["USDC"] = AssetBalance(
                balance="0.0000000",
                asset_type="credit_alphanum4",
                asset_code="USDC",
                asset_issuer="TEST_ISSUER",
            )
        return balances

    @classmethod
    def _build_public_key(cls) -> str:
        return f"G{uuid4().hex.upper()}"

    @classmethod
    def create_wallet(cls, payload: WalletCreateRequest) -> WalletCreateResponse:
        """Create a wallet with transactional integrity.
        
        This method ensures wallet creation and user linkage are atomic.
        If the operation fails at any point, the entire transaction rolls back,
        preventing detached wallet artifacts.
        
        Idempotent: Returns existing wallet if one already exists for the user.
        """
        # Check cache first for idempotency
        existing = cls._wallets_by_user.get(payload.user_id)
        if existing:
            return WalletCreateResponse(
                **existing.model_dump(),
                message="Wallet already exists for this user.",
            )

        # Use database transaction for atomic wallet creation
        with cls._get_db_session() as db:
            repo = WalletRepository(db)
            
            # Check database for existing wallet (idempotency check)
            existing_orm = repo.get_by_user_id(payload.user_id)
            if existing_orm:
                wallet = wallet_orm_to_pydantic(existing_orm)
                cls._wallets_by_user[payload.user_id] = wallet
                cls._wallets_by_address[wallet.public_key] = wallet
                return WalletCreateResponse(
                    **wallet.model_dump(),
                    message="Wallet already exists for this user.",
                )

            # Create wallet atomically in database
            try:
                public_key = cls._build_public_key()
                wallet_orm = repo.create_and_link_wallet(
                    user_id=payload.user_id,
                    public_key=public_key,
                    funded=False,
                    trustline_ready=False,
                )
                wallet = wallet_orm_to_pydantic(wallet_orm)
                cls._wallets_by_user[payload.user_id] = wallet
                cls._wallets_by_address[wallet.public_key] = wallet
                return WalletCreateResponse(
                    **wallet.model_dump(),
                    message="Wallet created. Please fund with at least 1 XLM to activate.",
                )
            except IntegrityError as e:
                # Handle race condition where wallet was created by another request
                db.rollback()
                existing_orm = repo.get_by_user_id(payload.user_id)
                if existing_orm:
                    wallet = wallet_orm_to_pydantic(existing_orm)
                    cls._wallets_by_user[payload.user_id] = wallet
                    cls._wallets_by_address[wallet.public_key] = wallet
                    return WalletCreateResponse(
                        **wallet.model_dump(),
                        message="Wallet already exists for this user.",
                    )
                raise ValueError(f"Failed to create wallet: {str(e)}")

    @classmethod
    def link_wallet(cls, payload: WalletLinkRequest) -> Wallet:
        """Link a wallet to a user with comprehensive conflict detection (BE-032).
        
        Conflict detection rules:
        1. User already linked to a different address → Reject (409 Conflict)
        2. Address already linked to a different user → Reject (409 Conflict)
        3. Same user + same address → Idempotent update (allowed)
        4. No conflicts → Create new link
        
        Thread-safe: uses simple lock to prevent race conditions during link operations.
        """
        link_key = f"{payload.user_id}:{payload.public_key}"
        
        # Simple lock to prevent concurrent link operations
        if cls._link_locks.get(link_key):
            raise ValueError(
                f"Link operation for user '{payload.user_id}' is already in progress."
            )
        
        try:
            cls._link_locks[link_key] = True
            
            # Use database transaction for atomic wallet linkage
            with cls._get_db_session() as db:
                repo = WalletRepository(db)
                
                try:
                    wallet_orm = repo.link_existing_wallet(
                        user_id=payload.user_id,
                        public_key=payload.public_key,
                        funded=payload.funded,
                        trustline_ready=payload.trustline_ready,
                    )
                    wallet = wallet_orm_to_pydantic(wallet_orm)
                    cls._wallets_by_user[payload.user_id] = wallet
                    cls._wallets_by_address[wallet.public_key] = wallet
                    return wallet
                except ValueError as e:
                    # Conflict errors from repository
                    raise e
                except IntegrityError as e:
                    db.rollback()
                    raise ValueError(f"Failed to link wallet: {str(e)}")
        finally:
            # Release lock
            cls._link_locks.pop(link_key, None)

    @classmethod
    def get_wallet(cls, user_id: str, refresh: bool = False) -> Wallet | None:
        wallet = cls._wallets_by_user.get(user_id)
        if not wallet:
            # Try database if not in cache
            with cls._get_db_session() as db:
                repo = WalletRepository(db)
                wallet_orm = repo.get_by_user_id(user_id)
                if not wallet_orm:
                    return None
                wallet = wallet_orm_to_pydantic(wallet_orm)
                cls._wallets_by_user[user_id] = wallet
                cls._wallets_by_address[wallet.public_key] = wallet
        if refresh or cls._is_stale(wallet):
            wallet = cls._refresh_wallet(wallet)
        else:
            # Mark as fresh if within TTL
            wallet = wallet.model_copy(update={"cache_status": "fresh"})
        return wallet

    @classmethod
    def get_balance(cls, address: str, refresh: bool = False) -> WalletBalanceResponse | None:
        if not cls._wallets_by_address.get(address):
            return None  # 404 -- address not in registry

        result: BalanceFetchResult = balance_fetch_adapter.fetch(
            address=address,
            live_fetcher=lambda addr: cls._build_live_balances(addr),
            force_refresh=refresh,
        )

        # Map adapter source to legacy cache_status for backwards compatibility
        legacy_cache_status = {
            "live": "live",
            "cache": "fresh",
            "stale_fallback": "stale",
        }.get(result.source, "fresh")

        return WalletBalanceResponse(
            address=address,
            balances=result.balances,
            last_updated=result.cached_at or cls._now(),
            cache_status=legacy_cache_status,
            cache_ttl_seconds=result.ttl_remaining,
            cached_at=result.cached_at,
            source=result.source,
            error_code=result.error.code if result.error else None,
            error_detail=result.error.detail if result.error else None,
        )

    @classmethod
    def get_status(cls, user_id: str, refresh: bool = False) -> WalletStatusResponse | None:
        wallet = cls.get_wallet(user_id, refresh=refresh)
        if not wallet:
            return None

        cache_ttl = cls._get_cache_ttl_remaining(wallet)
        return WalletStatusResponse(
            user_id=wallet.user_id,
            public_key=wallet.public_key,
            funded=wallet.funded,
            trustline_ready=wallet.trustline_ready,
            usable=wallet.funded and wallet.trustline_ready and wallet.active,
            active=wallet.active,
            last_updated=wallet.last_updated,
            cache_status=wallet.cache_status,
            cache_ttl_seconds=cache_ttl,
            cached_at=wallet.cached_at,
        )

    @classmethod
    def get_trustline(cls, user_id: str, refresh: bool = False) -> WalletTrustlineResponse | None:
        wallet = cls.get_wallet(user_id, refresh=refresh)
        if not wallet:
            return None

        error = None if wallet.trustline_ready else "Trustline not established. Fund wallet and set up USDC trustline."
        return WalletTrustlineResponse(
            user_id=wallet.user_id,
            public_key=wallet.public_key,
            trustline_ready=wallet.trustline_ready,
            trustline_error=error,
            cache_status=wallet.cache_status,
            cached_at=wallet.cached_at,
        )

    @classmethod
    def get_funding_state(cls, user_id: str, refresh: bool = False) -> WalletFundingStateResponse | None:
        wallet = cls.get_wallet(user_id, refresh=refresh)
        if not wallet:
            return None

        error = None if wallet.funded else "Wallet is not funded. Send at least 1 XLM to activate."
        return WalletFundingStateResponse(
            user_id=wallet.user_id,
            public_key=wallet.public_key,
            funded=wallet.funded,
            funding_error=error,
            cache_status=wallet.cache_status,
            cached_at=wallet.cached_at,
        )
