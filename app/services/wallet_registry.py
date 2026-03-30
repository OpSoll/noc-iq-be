from __future__ import annotations

from datetime import datetime, UTC
from uuid import uuid4

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


class WalletRegistry:
    _wallets_by_user: dict[str, Wallet] = {}
    _wallets_by_address: dict[str, Wallet] = {}

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    @classmethod
    def _build_public_key(cls) -> str:
        return f"G{uuid4().hex.upper()}"

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
    def get_balance(cls, address: str) -> WalletBalanceResponse | None:
        wallet = cls._wallets_by_address.get(address)
        if not wallet:
            return None

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
        return WalletBalanceResponse(
            address=address,
            balances=balances,
            last_updated=cls._now(),
        )

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

    @classmethod
    def get_trustline(cls, user_id: str) -> WalletTrustlineResponse | None:
        wallet = cls.get_wallet(user_id)
        if not wallet:
            return None

        error = None if wallet.trustline_ready else "Trustline not established. Fund wallet and set up USDC trustline."
        return WalletTrustlineResponse(
            user_id=wallet.user_id,
            public_key=wallet.public_key,
            trustline_ready=wallet.trustline_ready,
            trustline_error=error,
        )

    @classmethod
    def get_funding_state(cls, user_id: str) -> WalletFundingStateResponse | None:
        wallet = cls.get_wallet(user_id)
        if not wallet:
            return None

        error = None if wallet.funded else "Wallet is not funded. Send at least 1 XLM to activate."
        return WalletFundingStateResponse(
            user_id=wallet.user_id,
            public_key=wallet.public_key,
            funded=wallet.funded,
            funding_error=error,
        )
