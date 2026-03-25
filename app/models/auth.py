from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class AuthUser(BaseModel):
    id: str
    email: str
    full_name: Optional[str] = None
    role: str = "engineer"
    stellar_wallet: Optional[str] = None
    created_at: datetime


class LoginRequest(BaseModel):
    email: str
    password: str = Field(..., min_length=6)


class RegisterRequest(LoginRequest):
    full_name: str = Field(..., min_length=1)
    role: str = "engineer"


class AuthSessionResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = 3600
    user: AuthUser


class AuthLogoutResponse(BaseModel):
    message: str
