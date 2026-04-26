import pytest
from sqlalchemy.orm import Session
from app.services.auth_store import AuthStore
from app.models.auth import RegisterRequest, LoginRequest
from app.models.enums import Role
from app.models.orm.audit_log import AuditLogORM
from app.db.session import SessionLocal

@pytest.fixture
def db():
    # Use a real session but clean up or use a test DB if configured
    # For now, we'll assume the environment handles test DB or we use the default
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()

def test_registration_audit(db: Session):
    email = "audit_test@example.com"
    payload = RegisterRequest(
        email=email,
        password="Password123!",
        full_name="Audit Test",
        role=Role.engineer
    )
    
    AuthStore.register(payload, db=db)
    
    # Check audit log
    log = db.query(AuditLogORM).filter(AuditLogORM.email == email, AuditLogORM.event_type == "registration").first()
    assert log is not None
    assert log.details["role"] == "engineer"
    assert "password" not in log.details or log.details["password"] == "[REDACTED]"

def test_login_audit(db: Session):
    email = "login_audit@example.com"
    password = "Password123!"
    # Register first
    AuthStore.register(RegisterRequest(
        email=email,
        password=password,
        full_name="Login Audit",
        role=Role.engineer
    ), db=db)
    
    # Login
    AuthStore.login(LoginRequest(email=email, password=password), db=db)
    
def test_login_lockout(db: Session):
    email = "lockout_test@example.com"
    password = "Password123!"
    
    # Register user
    AuthStore.register(RegisterRequest(
        email=email,
        password=password,
        full_name="Lockout Test",
        role=Role.engineer
    ), db=db)
    
    # Try failed logins
    for i in range(5):
        try:
            AuthStore.login(LoginRequest(email=email, password="wrongpassword"), db=db)
            assert False, "Should have failed"
        except ValueError as e:
            assert "Invalid credentials" in str(e) or "locked" in str(e)
    
    # Next attempt should be locked
    try:
        AuthStore.login(LoginRequest(email=email, password=password), db=db)
        assert False, "Should be locked"
    except ValueError as e:
        assert "locked" in str(e)
    
    # Check audit log for lockout
    lockout_log = db.query(AuditLogORM).filter(
        AuditLogORM.email == email, 
        AuditLogORM.event_type == "account_locked"
    ).first()
    assert lockout_log is not None

def test_logout_audit(db: Session):
    email = "logout_audit@example.com"
    password = "Password123!"
    # Register and login
    AuthStore.register(RegisterRequest(
        email=email,
        password=password,
        full_name="Logout Audit",
        role=Role.engineer
    ), db=db)
    session_resp = AuthStore.login(LoginRequest(email=email, password=password), db=db)
    
    # Logout
    AuthStore.logout(session_resp.access_token, db=db)
    
    # Check audit log
    log = db.query(AuditLogORM).filter(AuditLogORM.email == email, AuditLogORM.event_type == "logout").first()
    assert log is not None

def test_audit_redaction(db: Session):
    from app.services.audit_log import AuditLogService
    
    AuditLogService.log_event(
        db, 
        "test_event", 
        email="test@leak.com", 
        details={"password": "secret_pass", "token": "sensitive_token", "safe": "data"}
    )
    
    log = db.query(AuditLogORM).filter(AuditLogORM.email == "test@leak.com").first()
    assert log.details["password"] == "[REDACTED]"
    assert log.details["token"] == "[REDACTED]"
    assert log.details["safe"] == "data"
