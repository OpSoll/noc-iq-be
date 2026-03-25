from datetime import datetime
from typing import Dict, Optional

from pydantic import BaseModel, Field


class Wallet(BaseModel):
    user_id: str
    public_key: str
    created_at: datetime
    last_updated: datetime
    funded: bool = False
    active: bool = True


class WalletCreateRequest(BaseModel):
    user_id: str = Field(..., min_length=1)


class WalletLinkRequest(BaseModel):
    user_id: str = Field(..., min_length=1)
    public_key: str = Field(..., min_length=2)
    funded: bool = False


class WalletCreateResponse(Wallet):
    message: str


class AssetBalance(BaseModel):
    balance: str
    asset_type: str
    asset_code: Optional[str] = None
    asset_issuer: Optional[str] = None


class WalletBalanceResponse(BaseModel):
    address: str
    balances: Dict[str, AssetBalance]
    last_updated: datetime
