from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel

from app.models.auth import (
    AuthLogoutResponse,
    AuthSessionResponse,
    AuthUser,
    LoginRequest,
    RegisterRequest,
)
from app.services.auth_store import AuthStore

router = APIRouter()


def _extract_bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    prefix = "Bearer "
    if not authorization.startswith(prefix):
        raise HTTPException(status_code=401, detail="Invalid Authorization header")
    return authorization[len(prefix) :]


class RefreshRequest(BaseModel):
    refresh_token: str


@router.post("/register", response_model=AuthUser, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest):
    try:
        return AuthStore.register(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/login", response_model=AuthSessionResponse)
def login(payload: LoginRequest):
    try:
        return AuthStore.login(payload)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.post("/refresh", response_model=AuthSessionResponse)
def refresh(payload: RefreshRequest):
    try:
        return AuthStore.refresh(payload.refresh_token)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.get("/me", response_model=AuthUser)
def me(authorization: str | None = Header(default=None)):
    token = _extract_bearer_token(authorization)
    user = AuthStore.get_user_for_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return user


@router.post("/logout", response_model=AuthLogoutResponse)
def logout(authorization: str | None = Header(default=None)):
    token = _extract_bearer_token(authorization)
    AuthStore.logout(token)
    return AuthLogoutResponse(message="Logged out successfully")


@router.get("/ping")
def auth_ping():
    return {"message": "auth ok"}
