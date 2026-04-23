from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4
from sqlalchemy.orm import Session

from app.models.auth import AuthSessionResponse, AuthUser, LoginRequest, RegisterRequest
from app.models.orm.user import UserORM
from app.repositories.user_repository import UserRepository, user_orm_to_pydantic
from app.repositories.session_repository import SessionRepository
from app.core.security import get_password_hash, verify_password, validate_password_policy
from app.services.audit_log import audit_log
from app.db.session import SessionLocal

TOKEN_TTL_SECONDS = 3600

class AuthStore:
    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    @classmethod
    def register(cls, payload: RegisterRequest, db: Session = None) -> AuthUser:
        if db is None:
            with SessionLocal() as db:
                return cls._register_with_db(payload, db)
        return cls._register_with_db(payload, db)

    @classmethod
    def _register_with_db(cls, payload: RegisterRequest, db: Session) -> AuthUser:
        user_repo = UserRepository(db)
        if user_repo.get_by_email(payload.email):
            raise ValueError("User already exists")

        if not validate_password_policy(payload.password):
            raise ValueError(
                "Password does not meet policy requirements (min 8 chars, "
                "uppercase, lowercase, digit, special char)"
            )

        hashed_password = get_password_hash(payload.password)
        user_id = f"user_{uuid4().hex[:8]}"
        
        orm_user = user_repo.create(
            user_id=user_id,
            email=payload.email,
            hashed_password=hashed_password,
            full_name=payload.full_name,
            role=payload.role
        )

        audit_log.log_event(
            db, 
            "registration", 
            email=payload.email, 
            details={"user_id": user_id, "role": payload.role}
        )
        
        return user_orm_to_pydantic(orm_user)

    @classmethod
    def login(cls, payload: LoginRequest, db: Session = None) -> AuthSessionResponse:
        if db is None:
            with SessionLocal() as db:
                return cls._login_with_db(payload, db)
        return cls._login_with_db(payload, db)

    @classmethod
    def _login_with_db(cls, payload: LoginRequest, db: Session) -> AuthSessionResponse:
        user_repo = UserRepository(db)
        session_repo = SessionRepository(db)
        
        stored_user = user_repo.get_by_email(payload.email)
        if not stored_user or not verify_password(payload.password, stored_user.hashed_password):
            audit_log.log_event(db, "login_failed", email=payload.email)
            raise ValueError("Invalid credentials")

        access_token = f"atk_{uuid4().hex}"
        refresh_token = f"rtk_{uuid4().hex}"
        expires_at = cls._now() + timedelta(seconds=TOKEN_TTL_SECONDS)
        
        session_repo.create_session(
            access_token=access_token,
            refresh_token=refresh_token,
            email=payload.email,
            expires_at=expires_at
        )

        audit_log.log_event(db, "login_success", email=payload.email)
        
        return AuthSessionResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=TOKEN_TTL_SECONDS,
            user=user_orm_to_pydantic(stored_user),
        )

    @classmethod
    def get_user_for_token(cls, token: str, db: Session = None) -> AuthUser | None:
        if db is None:
            with SessionLocal() as db:
                return cls._get_user_for_token_with_db(token, db)
        return cls._get_user_for_token_with_db(token, db)

    @classmethod
    def _get_user_for_token_with_db(cls, token: str, db: Session) -> AuthUser | None:
        session_repo = SessionRepository(db)
        user_repo = UserRepository(db)
        
        session = session_repo.get_session(token)
        if not session:
            return None
        
        # Check if expired
        # session.expires_at might be offset-naive or aware depending on how it was stored.
        # SQLAlchemy DateTime usually returns naive. We need to compare carefully.
        now = datetime.utcnow()
        expires_at = session.expires_at
        if expires_at.tzinfo is not None:
             now = datetime.now(UTC).replace(tzinfo=None) # Keep it naive for comparison if needed
             expires_at = expires_at.replace(tzinfo=None)

        if now > expires_at:
            session_repo.delete_session(token)
            return None
            
        stored_user = user_repo.get_by_email(session.email)
        return user_orm_to_pydantic(stored_user) if stored_user else None

    @classmethod
    def refresh(cls, refresh_token: str, db: Session = None) -> AuthSessionResponse:
        if db is None:
            with SessionLocal() as db:
                return cls._refresh_with_db(refresh_token, db)
        return cls._refresh_with_db(refresh_token, db)

    @classmethod
    def _refresh_with_db(cls, refresh_token: str, db: Session) -> AuthSessionResponse:
        session_repo = SessionRepository(db)
        user_repo = UserRepository(db)
        
        old_session = session_repo.get_session_by_refresh_token(refresh_token)
        if not old_session:
            raise ValueError("Invalid or expired refresh token")

        email = old_session.email
        session_repo.delete_session(old_session.access_token)

        stored_user = user_repo.get_by_email(email)
        if not stored_user:
            raise ValueError("User not found")

        new_access = f"atk_{uuid4().hex}"
        new_refresh = f"rtk_{uuid4().hex}"
        expires_at = cls._now() + timedelta(seconds=TOKEN_TTL_SECONDS)
        
        session_repo.create_session(
            access_token=new_access,
            refresh_token=new_refresh,
            email=email,
            expires_at=expires_at
        )

        audit_log.log_event(db, "refresh", email=email)
        
        return AuthSessionResponse(
            access_token=new_access,
            refresh_token=new_refresh,
            expires_in=TOKEN_TTL_SECONDS,
            user=user_orm_to_pydantic(stored_user),
        )

    @classmethod
    def logout(cls, token: str, db: Session = None) -> None:
        if db is None:
            with SessionLocal() as db:
                return cls._logout_with_db(token, db)
        return cls._logout_with_db(token, db)

    @classmethod
    def _logout_with_db(cls, token: str, db: Session) -> None:
        session_repo = SessionRepository(db)
        session = session_repo.get_session(token)
        if session:
            email = session.email
            session_repo.delete_session(token)
            audit_log.log_event(db, "logout", email=email)
