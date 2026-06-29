from typing import Optional
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from app.models.orm.wallet import WalletORM
from app.models.wallet import Wallet


def wallet_orm_to_pydantic(orm: WalletORM) -> Wallet:
    return Wallet(
        user_id=orm.user_id,
        public_key=orm.public_key,
        created_at=orm.created_at,
        last_updated=orm.last_updated,
        funded=orm.funded,
        active=orm.active,
        trustline_ready=orm.trustline_ready,
        cached_at=orm.cached_at,
        cache_status="fresh",
    )


class WalletRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_user_id(self, user_id: str) -> Optional[WalletORM]:
        return self.db.query(WalletORM).filter(WalletORM.user_id == user_id).first()

    def get_by_public_key(self, public_key: str) -> Optional[WalletORM]:
        return self.db.query(WalletORM).filter(WalletORM.public_key == public_key).first()

    def create_and_link_wallet(
        self,
        user_id: str,
        public_key: str,
        funded: bool = False,
        trustline_ready: bool = False,
    ) -> WalletORM:
        """
        Atomically create a wallet and link it to a user in a single transaction.
        
        This ensures transactional integrity - if any part fails, the entire operation
        rolls back, preventing detached wallet artifacts.
        
        Raises:
            IntegrityError: If user_id or public_key already exists (duplicate wallet)
        """
        now = datetime.now(timezone.utc)
        wallet = WalletORM(
            user_id=user_id,
            public_key=public_key,
            funded=funded,
            active=True,
            trustline_ready=trustline_ready,
            created_at=now,
            last_updated=now,
            cached_at=now,
        )
        self.db.add(wallet)
        self.db.commit()
        self.db.refresh(wallet)
        return wallet

    def link_existing_wallet(
        self,
        user_id: str,
        public_key: str,
        funded: bool = False,
        trustline_ready: bool = False,
    ) -> WalletORM:
        """
        Link an existing wallet to a user with idempotent update.
        
        This method handles the case where a wallet already exists and needs to be
        linked/updated for a user. It performs conflict detection to ensure:
        - User is not already linked to a different wallet
        - Wallet is not already linked to a different user
        
        Raises:
            ValueError: If user already linked to different wallet or wallet linked to different user
        """
        now = datetime.now(timezone.utc)
        
        # Check if user already has a wallet
        existing_by_user = self.get_by_user_id(user_id)
        if existing_by_user and existing_by_user.public_key != public_key:
            raise ValueError(
                f"User '{user_id}' is already linked to wallet '{existing_by_user.public_key}'. "
                f"Cannot link to '{public_key}'."
            )
        
        # Check if wallet is already linked to a different user
        existing_by_address = self.get_by_public_key(public_key)
        if existing_by_address and existing_by_address.user_id != user_id:
            raise ValueError(
                f"Wallet address '{public_key}' is already linked to user '{existing_by_address.user_id}'. "
                f"Cannot link to '{user_id}'."
            )
        
        # Idempotent: if same user + same address, just update metadata
        if existing_by_user and existing_by_user.public_key == public_key:
            existing_by_user.funded = funded
            existing_by_user.trustline_ready = trustline_ready
            existing_by_user.active = True
            existing_by_user.last_updated = now
            existing_by_user.cached_at = now
            self.db.commit()
            self.db.refresh(existing_by_user)
            return existing_by_user
        
        # No conflicts - create new wallet (should not reach here if wallet exists)
        return self.create_and_link_wallet(user_id, public_key, funded, trustline_ready)

    def update_wallet(
        self,
        user_id: str,
        funded: Optional[bool] = None,
        trustline_ready: Optional[bool] = None,
        active: Optional[bool] = None,
    ) -> Optional[WalletORM]:
        """Update wallet fields. Returns updated ORM or None if not found."""
        wallet = self.get_by_user_id(user_id)
        if not wallet:
            return None
        
        if funded is not None:
            wallet.funded = funded
        if trustline_ready is not None:
            wallet.trustline_ready = trustline_ready
        if active is not None:
            wallet.active = active
        
        wallet.last_updated = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(wallet)
        return wallet
