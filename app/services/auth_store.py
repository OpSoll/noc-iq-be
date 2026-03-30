from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.models.auth import AuthSessionResponse, AuthUser, LoginRequest, RegisterRequest

TOKEN_TTL_SECONDS = 3600


@dataclass
class _StoredUser:
    user: AuthUser
    password: str


@dataclass
class _Session:
    email: str
    expires_at: datetime
    refresh_token: str


class AuthStore:
    _users_by_email: dict[str, _StoredUser] = {}
    _sessions: dict[str, _Session] = {}          # access_token -> _Session
    _refresh_tokens: dict[str, str] = {}          # refresh_token -> access_token

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    @classmethod
    def register(cls, payload: RegisterRequest) -> AuthUser:
        if payload.email in cls._users_by_email:
            raise ValueError("User already exists")

        user = AuthUser(
            id=f"user_{uuid4().hex[:8]}",
            email=payload.email,
            full_name=payload.full_name,
            role=payload.role,
            created_at=cls._now(),
        )
        cls._users_by_email[payload.email] = _StoredUser(user=user, password=payload.password)
        return user

    @classmethod
    def login(cls, payload: LoginRequest) -> AuthSessionResponse:
        stored = cls._users_by_email.get(payload.email)
        if not stored or stored.password != payload.password:
            raise ValueError("Invalid credentials")

        access_token = f"atk_{uuid4().hex}"
        refresh_token = f"rtk_{uuid4().hex}"
        expires_at = cls._now() + timedelta(seconds=TOKEN_TTL_SECONDS)
        cls._sessions[access_token] = _Session(
            email=payload.email,
            expires_at=expires_at,
            refresh_token=refresh_token,
        )
        cls._refresh_tokens[refresh_token] = access_token
        return AuthSessionResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=TOKEN_TTL_SECONDS,
            user=stored.user,
        )

    @classmethod
    def get_user_for_token(cls, token: str) -> AuthUser | None:
        session = cls._sessions.get(token)
        if not session:
            return None
        if cls._now() > session.expires_at:
            cls._invalidate_session(token)
            return None
        stored = cls._users_by_email.get(session.email)
        return stored.user if stored else None

    @classmethod
    def refresh(cls, refresh_token: str) -> AuthSessionResponse:
        old_access = cls._refresh_tokens.get(refresh_token)
        if not old_access:
            raise ValueError("Invalid or expired refresh token")

        session = cls._sessions.get(old_access)
        if not session:
            raise ValueError("Invalid or expired refresh token")

        email = session.email
        cls._invalidate_session(old_access)

        stored = cls._users_by_email.get(email)
        if not stored:
            raise ValueError("User not found")

        new_access = f"atk_{uuid4().hex}"
        new_refresh = f"rtk_{uuid4().hex}"
        expires_at = cls._now() + timedelta(seconds=TOKEN_TTL_SECONDS)
        cls._sessions[new_access] = _Session(
            email=email,
            expires_at=expires_at,
            refresh_token=new_refresh,
        )
        cls._refresh_tokens[new_refresh] = new_access
        return AuthSessionResponse(
            access_token=new_access,
            refresh_token=new_refresh,
            expires_in=TOKEN_TTL_SECONDS,
            user=stored.user,
        )

    @classmethod
    def logout(cls, token: str) -> None:
        cls._invalidate_session(token)

    @classmethod
    def _invalidate_session(cls, access_token: str) -> None:
        session = cls._sessions.pop(access_token, None)
        if session:
            cls._refresh_tokens.pop(session.refresh_token, None)
