from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from app.models.auth import AuthSessionResponse, AuthUser, LoginRequest, RegisterRequest


@dataclass
class _StoredUser:
    user: AuthUser
    password: str


class AuthStore:
    _users_by_email: dict[str, _StoredUser] = {}
    _sessions: dict[str, str] = {}

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
        cls._sessions[access_token] = payload.email
        return AuthSessionResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            user=stored.user,
        )

    @classmethod
    def get_user_for_token(cls, token: str) -> AuthUser | None:
        email = cls._sessions.get(token)
        if not email:
            return None
        stored = cls._users_by_email.get(email)
        return stored.user if stored else None

    @classmethod
    def logout(cls, token: str) -> None:
        cls._sessions.pop(token, None)
