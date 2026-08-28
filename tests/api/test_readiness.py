from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_readiness_probe_ok():
    response = client.get("/api/v1/system/ready")
    assert response.status_code in {200, 503}
    data = response.json()
    assert data["status"] in {"ok", "degraded"}
    assert "database" in data["dependencies"]
    assert "redis" in data["dependencies"]
    assert data["dependencies"]["database"]["status"] in {"up", "down"}
