import pytest
from sqlalchemy.orm import Session
from app.services.auth_store import AuthStore
from app.models.auth import RegisterRequest, LoginRequest
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
        role="engineer"
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
        role="engineer"
    ), db=db)
    
    # Login
    AuthStore.login(LoginRequest(email=email, password=password), db=db)
    
    # Check audit log
    log = db.query(AuditLogORM).filter(AuditLogORM.email == email, AuditLogORM.event_type == "login_success").first()
    assert log is not None
    assert "password" not in log.details or log.details["password"] == "[REDACTED]"

def test_logout_audit(db: Session):
    email = "logout_audit@example.com"
    password = "Password123!"
    # Register and login
    AuthStore.register(RegisterRequest(
        email=email,
        password=password,
        full_name="Logout Audit",
        role="engineer"
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
