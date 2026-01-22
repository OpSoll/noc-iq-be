from datetime import datetime
from pydantic import BaseModel


class Wallet(BaseModel):
    user_id: str
    public_key: str
    created_at: datetime
    funded: bool = False