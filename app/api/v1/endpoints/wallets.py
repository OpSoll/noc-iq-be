from fastapi import APIRouter, HTTPException, status

from app.models.wallet import (
    Wallet,
    WalletBalanceResponse,
    WalletCreateRequest,
    WalletCreateResponse,
    WalletLinkRequest,
    WalletStatusResponse,
)
from app.services.wallet_registry import WalletRegistry

router = APIRouter()


@router.post("/create", response_model=WalletCreateResponse, status_code=status.HTTP_201_CREATED)
def create_wallet(payload: WalletCreateRequest):
    return WalletRegistry.create_wallet(payload)


@router.post("/link", response_model=Wallet)
def link_wallet(payload: WalletLinkRequest):
    return WalletRegistry.link_wallet(payload)


@router.get("/ping")
def wallets_ping():
    return {"message": "wallets ok"}


@router.get("/{user_id}", response_model=Wallet)
def get_wallet(user_id: str):
    wallet = WalletRegistry.get_wallet(user_id)
    if not wallet:
        raise HTTPException(status_code=404, detail="Wallet not found")
    return wallet


@router.get("/{user_id}/status", response_model=WalletStatusResponse)
def get_wallet_status(user_id: str):
    wallet_status = WalletRegistry.get_status(user_id)
    if not wallet_status:
        raise HTTPException(status_code=404, detail="Wallet not found")
    return wallet_status


@router.get("/{address}/balance", response_model=WalletBalanceResponse)
def get_wallet_balance(address: str):
    balance = WalletRegistry.get_balance(address)
    if not balance:
        raise HTTPException(status_code=404, detail="Wallet not found")
    return balance
