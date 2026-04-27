from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.models.enums import Role


class AuthUser(BaseModel):
    id: str
    email: str
    full_name: Optional[str] = None
    role: Role = Role.engineer
    stellar_wallet: Optional[str] = None
    created_at: datetime


class LoginRequest(BaseModel):
    email: str
    password: str = Field(..., min_length=6)


class RegisterRequest(LoginRequest):
    full_name: str = Field(..., min_length=1)
    role: Role = Role.engineer


class AuthSessionResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = 3600
    user: AuthUser


class AuthLogoutResponse(BaseModel):
    message: str


class SessionInfo(BaseModel):
    """Session information for session inventory (excludes full token material)."""
    access_token_preview: str | None = None
    refresh_token_preview: str | None = None
    email: str
    expires_at: datetime
    created_at: datetime
    is_active: bool


class SessionInventoryResponse(BaseModel):
    """Response for session inventory endpoint."""
    sessions: list[SessionInfo]
    total_count: int
    active_count: int


class LogoutAllSessionsResponse(BaseModel):
    """Response for logout-all-sessions endpoint."""
    message: str
    sessions_invalidated: int


class ProfileUpdateRequest(BaseModel):
    """Allowed mutable profile fields. Role and email changes are not permitted here."""
    full_name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    stellar_wallet: Optional[str] = Field(default=None, max_length=255)
