from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class TxProvenanceResponse(BaseModel):
    tx_hash: str
    network: str
    status: str
    submitted_at: Optional[datetime] = None
    confirmed_at: Optional[datetime] = None
    verified_at: Optional[datetime] = None
    block_number: Optional[int] = None


class TxVerifyRequest(BaseModel):
    force: bool = Field(default=False, description="Force re-verification even if already verified")


class TxVerifyResponse(BaseModel):
    tx_hash: str
    status: str
    verified_at: Optional[datetime] = None
    message: str
