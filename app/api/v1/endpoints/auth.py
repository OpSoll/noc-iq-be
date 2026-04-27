from fastapi import APIRouter, Header, HTTPException, status, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.models.auth import (
    AuthLogoutResponse,
    AuthSessionResponse,
    AuthUser,
    LoginRequest,
    RegisterRequest,
    SessionInventoryResponse,
    SessionInfo,
    LogoutAllSessionsResponse,
)
from app.services.auth_store import AuthStore
from app.db.session import get_db
from app.core.security import get_current_user, require_admin
from app.core.rate_limiter import rate_limiter

router = APIRouter()

"""
Auth Rate Limiting and Lockout Strategy:

1. IP-based Rate Limiting:
   - Max 10 requests per 5-minute window per IP for login/refresh endpoints
   - Returns 429 Too Many Requests when exceeded

2. Account Lockout:
   - After 5 failed login attempts, account is locked for 15 minutes
   - Failed attempts reset on successful login
   - Refresh tokens are also blocked for locked accounts

3. Audit Logging:
   - All failed attempts are logged
   - Account lockouts are logged with duration

Configuration (in app.core.config):
- AUTH_MAX_FAILED_ATTEMPTS: 5
- AUTH_LOCKOUT_DURATION_MINUTES: 15
- AUTH_RATE_LIMIT_REQUESTS: 10
- AUTH_RATE_LIMIT_WINDOW_SECONDS: 300
"""


from app.core.config import settings


def _get_client_ip(request: Request) -> str:
    """
    Extract the real client IP, respecting TRUSTED_PROXY_COUNT.

    When TRUSTED_PROXY_COUNT > 0 the app sits behind that many trusted proxy
    hops.  We take the Nth-from-the-right entry in X-Forwarded-For (where N =
    TRUSTED_PROXY_COUNT) so that a client cannot spoof its IP by injecting
    extra entries at the left of the header.

    When TRUSTED_PROXY_COUNT == 0 (default) we ignore forwarded headers
    entirely and use the direct connection address.
    """
    trusted = settings.TRUSTED_PROXY_COUNT
    if trusted > 0:
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            parts = [p.strip() for p in forwarded.split(",")]
            # The rightmost `trusted` entries are added by our own proxies.
            # The entry just to the left of those is the real client.
            idx = max(len(parts) - trusted, 0)
            return parts[idx]
    return request.client.host if request.client else "unknown"


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
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    try:
        return AuthStore.register(payload, db=db)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/login", response_model=AuthSessionResponse)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    client_ip = _get_client_ip(request)
    
    # Rate limit by IP
    if not rate_limiter.is_allowed(f"login_ip_{client_ip}"):
        raise HTTPException(
            status_code=429, 
            detail="Too many login attempts from this IP. Please try again later."
        )
    
    try:
        return AuthStore.login(payload, db=db)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.post("/refresh", response_model=AuthSessionResponse)
def refresh(payload: RefreshRequest, request: Request, db: Session = Depends(get_db)):
    client_ip = _get_client_ip(request)
    
    # Rate limit by IP
    if not rate_limiter.is_allowed(f"refresh_ip_{client_ip}"):
        raise HTTPException(
            status_code=429, 
            detail="Too many refresh attempts from this IP. Please try again later."
        )
    
    try:
        return AuthStore.refresh(payload.refresh_token, db=db)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.get("/me", response_model=AuthUser)
def me(current_user: AuthUser = Depends(get_current_user)):
    return current_user


@router.post("/logout", response_model=AuthLogoutResponse)
def logout(
    authorization: str | None = Header(default=None), db: Session = Depends(get_db)
):
    token = _extract_bearer_token(authorization)
    AuthStore.logout(token, db=db)
    return AuthLogoutResponse(message="Logged out successfully")


@router.get("/sessions", response_model=SessionInventoryResponse)
def get_session_inventory(
    current_user: AuthUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get all active sessions for the current user."""
    sessions = AuthStore.get_user_sessions(current_user.email, db=db)
    
    session_infos = [SessionInfo(**s) for s in sessions]
    active_count = sum(1 for s in session_infos if s.is_active)
    
    return SessionInventoryResponse(
        sessions=session_infos,
        total_count=len(session_infos),
        active_count=active_count,
    )


@router.get("/admin/sessions/{email}", response_model=SessionInventoryResponse)
def get_admin_session_inventory(
    email: str,
    admin_user: AuthUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Admin endpoint to get all sessions for a specific user."""
    sessions = AuthStore.get_user_sessions(email, db=db)
    
    session_infos = [SessionInfo(**s) for s in sessions]
    active_count = sum(1 for s in session_infos if s.is_active)
    
    return SessionInventoryResponse(
        sessions=session_infos,
        total_count=len(session_infos),
        active_count=active_count,
    )


@router.post("/logout-all", response_model=LogoutAllSessionsResponse)
def logout_all_sessions(
    current_user: AuthUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Invalidate all active sessions for the current user."""
    count = AuthStore.logout_all_sessions(current_user.email, db=db)
    return LogoutAllSessionsResponse(
        message=f"Logged out from {count} session(s)",
        sessions_invalidated=count,
    )


@router.post("/admin/logout-all/{email}", response_model=LogoutAllSessionsResponse)
def admin_logout_all_sessions(
    email: str,
    admin_user: AuthUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Admin endpoint to invalidate all sessions for a specific user."""
    count = AuthStore.logout_all_sessions(email, db=db)
    return LogoutAllSessionsResponse(
        message=f"Logged out user {email} from {count} session(s)",
        sessions_invalidated=count,
    )


@router.get("/ping")
def auth_ping():
    return {"message": "auth ok"}
