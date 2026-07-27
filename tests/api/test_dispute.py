from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from app.main import app

client = TestClient(app)

def test_sla_dispute_workflow(db: Session):
    # Open dispute
    payload = {
        "sla_id": 101,
        "reason": "Unexpected downtime not our fault"
    }
    response_open = client.post("/api/v1/disputes/", json=payload)
    assert response_open.status_code == 200
    data = response_open.json()
    assert data["state"] == "OPEN"
    dispute_id = data["id"]
    
    # Update to resolved
    update_payload = {
        "state": "RESOLVED",
        "resolution_notes": "Granted credit to customer"
    }
    response_update = client.patch(f"/api/v1/disputes/{dispute_id}", json=update_payload)
    assert response_update.status_code == 200
    updated_data = response_update.json()
    assert updated_data["state"] == "RESOLVED"
    assert updated_data["resolution_notes"] == "Granted credit to customer"
