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
    trustline_ready: bool = False


class WalletCreateRequest(BaseModel):
    user_id: str = Field(..., min_length=1)


class WalletLinkRequest(BaseModel):
    user_id: str = Field(..., min_length=1)
    public_key: str = Field(..., min_length=2)
    funded: bool = False
    trustline_ready: bool = False


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


class WalletStatusResponse(BaseModel):
    user_id: str
    public_key: str
    funded: bool
    trustline_ready: bool
    usable: bool
    active: bool
    last_updated: datetime


class WalletTrustlineResponse(BaseModel):
    user_id: str
    public_key: str
    trustline_ready: bool
    trustline_error: Optional[str] = None


class WalletFundingStateResponse(BaseModel):
    user_id: str
    public_key: str
    funded: bool
    funding_error: Optional[str] = None
