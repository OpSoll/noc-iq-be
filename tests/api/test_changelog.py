from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from app.main import app

import uuid


def test_api_changelog(client, db: Session):
    v = f"v1.2.{uuid.uuid4().hex[:6]}"
    # Add entry
    payload = {
        "version": v,
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
    assert any(item["version"] == v for item in data)
