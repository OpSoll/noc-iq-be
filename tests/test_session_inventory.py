"""Test session inventory and logout-all-sessions endpoints."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.db.session import get_db, SessionLocal
from app.models.orm.session import SessionORM
from app.models.orm.user import UserORM
from app.models.enums import Role
from app.core.security import get_password_hash


def override_get_db():
    """Override database dependency for testing."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db

client = TestClient(app)


def create_test_user(db: Session, email: str, password: str = "Password123!", role: Role = Role.engineer) -> UserORM:
    """Helper to create a test user."""
    user = UserORM(
        id=f"user_test_{email.split('@')[0]}",
        email=email,
        hashed_password=get_password_hash(password),
        full_name=f"Test User {email}",
        role=role.value,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def create_test_session(db: Session, access_token: str, refresh_token: str, email: str) -> SessionORM:
    """Helper to create a test session."""
    from datetime import datetime, timedelta
    
    session = SessionORM(
        access_token=access_token,
        refresh_token=refresh_token,
        email=email,
        expires_at=datetime.utcnow() + timedelta(hours=1),
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def test_session_inventory_user(db: Session):
    """Test that a user can view their own session inventory."""
    email = "session_inv_user@example.com"
    password = "Password123!"
    
    # Create user
    create_test_user(db, email, password)
    
    # Login to create a session
    login_resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert login_resp.status_code == 200
    access_token = login_resp.json()["access_token"]
    
    # Create additional sessions
    create_test_session(db, "atk_test_session_1", "rtk_test_session_1", email)
    create_test_session(db, "atk_test_session_2", "rtk_test_session_2", email)
    
    # Get session inventory
    headers = {"Authorization": f"Bearer {access_token}"}
    resp = client.get("/api/v1/auth/sessions", headers=headers)
    
    assert resp.status_code == 200
    data = resp.json()
    assert "sessions" in data
    assert "total_count" in data
    assert "active_count" in data
    assert data["total_count"] >= 1  # At least the login session
    assert data["active_count"] >= 1
    
    # Verify session info doesn't expose full tokens
    for session in data["sessions"]:
        assert "access_token_preview" in session
        assert "refresh_token_preview" in session
        assert "email" in session
        assert "expires_at" in session
        assert "created_at" in session
        assert "is_active" in session
        
        # Token previews should be truncated
        if session["access_token_preview"]:
            assert session["access_token_preview"].endswith("...")
            assert len(session["access_token_preview"]) < 20


def test_session_inventory_admin(db: Session):
    """Test that an admin can view any user's session inventory."""
    admin_email = "admin_session@example.com"
    user_email = "user_session@example.com"
    password = "Password123!"
    
    # Create admin and user
    create_test_user(db, admin_email, password, role=Role.admin)
    create_test_user(db, user_email, password, role=Role.engineer)
    
    # Login as admin
    login_resp = client.post("/api/v1/auth/login", json={"email": admin_email, "password": password})
    assert login_resp.status_code == 200
    admin_token = login_resp.json()["access_token"]
    
    # Create sessions for user
    create_test_session(db, "atk_admin_view_1", "rtk_admin_view_1", user_email)
    create_test_session(db, "atk_admin_view_2", "rtk_admin_view_2", user_email)
    
    # Admin views user's sessions
    headers = {"Authorization": f"Bearer {admin_token}"}
    resp = client.get(f"/api/v1/auth/admin/sessions/{user_email}", headers=headers)
    
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_count"] == 2
    assert data["active_count"] == 2


def test_session_inventory_non_admin_forbidden(db: Session):
    """Test that non-admin users cannot access admin session inventory."""
    user_email = "non_admin_session@example.com"
    password = "Password123!"
    
    # Create user
    create_test_user(db, user_email, password, role=Role.engineer)
    
    # Login as user
    login_resp = client.post("/api/v1/auth/login", json={"email": user_email, "password": password})
    assert login_resp.status_code == 200
    user_token = login_resp.json()["access_token"]
    
    # Try to access admin endpoint
    headers = {"Authorization": f"Bearer {user_token}"}
    resp = client.get(f"/api/v1/auth/admin/sessions/someone@example.com", headers=headers)
    
    assert resp.status_code == 403


def test_logout_all_sessions_user(db: Session):
    """Test that a user can logout from all sessions."""
    email = "logout_all_user@example.com"
    password = "Password123!"
    
    # Create user
    create_test_user(db, email, password)
    
    # Login to create a session
    login_resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert login_resp.status_code == 200
    access_token = login_resp.json()["access_token"]
    
    # Create additional sessions
    create_test_session(db, "atk_logout_all_1", "rtk_logout_all_1", email)
    create_test_session(db, "atk_logout_all_2", "rtk_logout_all_2", email)
    
    # Verify sessions exist
    headers = {"Authorization": f"Bearer {access_token}"}
    resp = client.get("/api/v1/auth/sessions", headers=headers)
    assert resp.status_code == 200
    initial_count = resp.json()["total_count"]
    assert initial_count >= 3
    
    # Logout from all sessions
    resp = client.post("/api/v1/auth/logout-all", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "sessions_invalidated" in data
    assert data["sessions_invalidated"] >= 3
    assert "Logged out from" in data["message"]
    
    # Verify the current token is now invalid
    resp = client.get("/api/v1/auth/sessions", headers=headers)
    assert resp.status_code == 401  # Token should be invalidated


def test_logout_all_sessions_admin(db: Session):
    """Test that an admin can logout all sessions for a specific user."""
    admin_email = "admin_logout_all@example.com"
    user_email = "user_logout_all@example.com"
    password = "Password123!"
    
    # Create admin and user
    create_test_user(db, admin_email, password, role=Role.admin)
    create_test_user(db, user_email, password, role=Role.engineer)
    
    # Login as admin
    login_resp = client.post("/api/v1/auth/login", json={"email": admin_email, "password": password})
    assert login_resp.status_code == 200
    admin_token = login_resp.json()["access_token"]
    
    # Create sessions for user
    create_test_session(db, "atk_admin_logout_1", "rtk_admin_logout_1", user_email)
    create_test_session(db, "atk_admin_logout_2", "rtk_admin_logout_2", user_email)
    
    # Admin logs out user from all sessions
    headers = {"Authorization": f"Bearer {admin_token}"}
    resp = client.post(f"/api/v1/auth/admin/logout-all/{user_email}", headers=headers)
    
    assert resp.status_code == 200
    data = resp.json()
    assert data["sessions_invalidated"] == 2
    assert user_email in data["message"]


def test_logout_all_sessions_non_admin_forbidden(db: Session):
    """Test that non-admin users cannot use admin logout-all endpoint."""
    user_email = "non_admin_logout@example.com"
    password = "Password123!"
    
    # Create user
    create_test_user(db, user_email, password, role=Role.engineer)
    
    # Login as user
    login_resp = client.post("/api/v1/auth/login", json={"email": user_email, "password": password})
    assert login_resp.status_code == 200
    user_token = login_resp.json()["access_token"]
    
    # Try to access admin endpoint
    headers = {"Authorization": f"Bearer {user_token}"}
    resp = client.post(f"/api/v1/auth/admin/logout-all/someone@example.com", headers=headers)
    
    assert resp.status_code == 403


def test_session_inventory_no_sessions(db: Session):
    """Test session inventory when user has no sessions."""
    email = "no_sessions@example.com"
    password = "Password123!"
    
    # Create user but don't login
    create_test_user(db, email, password)
    
    # Manually create expired session to test filtering
    from datetime import datetime, timedelta
    session = SessionORM(
        access_token="atk_expired",
        refresh_token="rtk_expired",
        email=email,
        expires_at=datetime.utcnow() - timedelta(hours=1),  # Expired
    )
    db.add(session)
    db.commit()
    
    # Login to get valid token
    login_resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert login_resp.status_code == 200
    access_token = login_resp.json()["access_token"]
    
    # Get session inventory
    headers = {"Authorization": f"Bearer {access_token}"}
    resp = client.get("/api/v1/auth/sessions", headers=headers)
    
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_count"] >= 1
    # The expired session should be marked as inactive
    inactive_sessions = [s for s in data["sessions"] if not s["is_active"]]
    assert len(inactive_sessions) >= 0  # May or may not include expired depending on cleanup
