from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from app.main import app

client = TestClient(app)

def test_api_changelog(db: Session):
    # Add entry
    payload = {
        "version": "v1.2.0",
        "description": "Added idempotency keys for financial endpoints",
        "breaking_changes": False
    }
    response_post = client.post("/api/v1/changelog/", json=payload)
    assert response_post.status_code == 200
    
    # Get entries
    response_get = client.get("/api/v1/changelog/")
    assert response_get.status_code == 200
    data = response_get.json()
    assert len(data) >= 1
    assert data[0]["version"] == "v1.2.0"
