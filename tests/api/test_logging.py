from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from app.main import app

client = TestClient(app)

def test_request_log_pii_redaction(db: Session):
    payload = {
        "method": "POST",
        "path": "/api/users",
        "status_code": 201,
        "payload": {
            "username": "john_doe",
            "email": "john@example.com",
            "metadata": {
                "phone": "555-1234"
            }
        }
    }
    
    response = client.post("/api/v1/logging/log", json=payload)
    assert response.status_code == 200
    data = response.json()
    
    logged_payload = data["payload"]
    assert logged_payload["username"] == "john_doe"
    assert logged_payload["email"] == "[REDACTED]"
    assert logged_payload["metadata"]["phone"] == "[REDACTED]"
