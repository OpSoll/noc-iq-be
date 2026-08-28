from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from app.main import app

client = TestClient(app)

def test_bulk_outage_creation(db: Session):
    payload = {
        "outages": [
            {
                "service_name": "API Gateway",
                "description": "High latency in region US-East",
                "severity": "HIGH"
            },
            {
                "service_name": "Redis Cache",
                "description": "Cache misses spiking",
                "severity": "MEDIUM"
            }
        ]
    }
    
    response = client.post("/api/v1/outages/bulk", json=payload, headers={"Authorization": "Bearer test-engineer-token"})
    assert response.status_code == 200
    data = response.json()
    assert data["successful"] == 2
    assert data["failed"] == 0
    assert "Successfully" in data["message"]
